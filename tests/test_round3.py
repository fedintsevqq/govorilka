"""Версия пробной сборки, «Что нового», автозапуск при установке и сброс разрешений при смене подписи."""

from dataclasses import replace

import pytest

from govorilka import features, version
from govorilka.config import Settings


class Listener:
    def __getattr__(self, name):
        return lambda *a, **k: None


class NoHotkeys:
    def set_chord(self, chord): ...


@pytest.fixture
def make(tmp_path, monkeypatch):
    from govorilka import controller as controller_module

    saved = []
    monkeypatch.setattr(controller_module, "save_settings", lambda s: saved.append(s))

    def build(**kw):
        return controller_module.Controller(Listener(), Settings(transcripts_dir=str(tmp_path), **kw), hotkeys=NoHotkeys())

    build.saved = saved
    build.module = controller_module
    return build


# ---------- версии ----------


def test_trial_build_is_newer_than_any_release_and_never_offered_an_update(monkeypatch):
    monkeypatch.setattr(version, "VERSION", "dev")
    monkeypatch.setattr(version.sys, "frozen", True, raising=False)
    assert version.is_trial_build() and not version.is_newer("9.9.9", "dev")
    monkeypatch.setattr(version.sys, "frozen", False, raising=False)
    assert not version.is_trial_build() and version.is_newer("0.0.1", "dev")  # из исходников любая версия новее


# ---------- что нового ----------


def test_features_after_an_update_are_only_what_came_since_then():
    new = features.new_since("0.3.2")
    assert {f.key for f in new} >= {"dictation", "summary", "catalog", "update", "select", "autostart"}
    assert all(f.since == "0.3.3" for f in new)
    assert features.new_since("0.3.3") == []
    assert len(features.new_since("0.0.0")) == len(features.FEATURES)
    assert features.guide_version() == "0.3.3"  # у пробной сборки номера нет: берётся самая свежая версия из списка


def test_every_feature_text_fills_in_for_both_systems():
    for mac in (False, True):
        for f in features.for_platform(features.FEATURES, mac):
            card = features.render(f, "Ctrl + Shift", "Ctrl + Alt", mac)
            assert "{" not in card["body"] and card["title"]
    assert "⌘C" in features.render(next(f for f in features.FEATURES if f.key == "select"), "x", "y", True)["body"]
    assert "permissions" not in [f.key for f in features.for_platform(features.FEATURES, False)]  # разрешения — только Mac


def test_whats_new_is_shown_once_after_an_update_and_never_to_a_new_user(make):
    old = make(tour_done=True, last_seen_version="0.3.2")
    assert old.whats_new_since() == "0.3.2"
    assert old.settings.last_seen_version == "0.3.3" and old.whats_new_since() is None  # второй раз не показываем
    veteran = make(tour_done=True)  # пользовался до появления окна: отсчёт от 0.3.2
    assert veteran.whats_new_since() == features.BASELINE
    newcomer = make(tour_done=False)  # новому человеку хватит знакомства
    assert newcomer.whats_new_since() is None and newcomer.settings.last_seen_version == "0.3.3"


# ---------- автозапуск при установке ----------


def test_autostart_is_switched_on_once_on_a_fresh_install_only(make, monkeypatch):
    calls = []
    monkeypatch.setattr(make.module.autostart, "set_enabled", lambda on, *a, **k: calls.append(on))
    monkeypatch.setattr(make.module, "FROZEN", True)
    fresh = make(tour_done=False)
    fresh.init_autostart()
    assert calls == [True] and fresh.settings.autostart_initialized
    fresh.init_autostart()
    assert calls == [True]  # второй раз не трогаем: человек мог выключить
    veteran = make(tour_done=True)
    veteran.init_autostart()
    assert calls == [True] and veteran.settings.autostart_initialized  # у тех, кто уже пользовался, ничего не включаем


def test_autostart_failure_does_not_break_start(make, monkeypatch):
    def refuse(*a, **k):
        raise OSError("нет доступа")

    monkeypatch.setattr(make.module.autostart, "set_enabled", refuse)
    monkeypatch.setattr(make.module, "FROZEN", True)
    make(tour_done=False).init_autostart()  # без исключения


# ---------- смена подписи на Mac ----------


def test_permissions_are_reset_once_when_the_signature_changes(make, monkeypatch):
    resets = []
    monkeypatch.setattr(make.module.sys, "platform", "darwin")
    monkeypatch.setattr(make.module, "FROZEN", True)
    monkeypatch.setattr(make.module.permissions, "reset_all", lambda: resets.append(1) or [])
    monkeypatch.setattr(make.module.permissions, "signing_identity", lambda: "abc123")
    veteran = make(tour_done=True)  # раньше пользовался: подпись была временной, в списке разрешений остался старый переключатель
    assert veteran.check_signing_change() is True and resets == [1] and veteran.settings.signing_id == "abc123"
    assert veteran.check_signing_change() is False and resets == [1]  # подпись та же: больше не сбрасываем
    newcomer = make(tour_done=False)
    assert newcomer.check_signing_change() is False and resets == [1] and newcomer.settings.signing_id == "abc123"
    same = make(tour_done=True, signing_id="abc123")
    assert same.check_signing_change() is False and resets == [1]


def test_signing_check_is_skipped_outside_the_mac_app(make, monkeypatch):
    monkeypatch.setattr(make.module.permissions, "signing_identity", lambda: "abc")
    assert make(tour_done=True).check_signing_change() is False  # Windows и запуск из исходников


def test_signing_identity_is_the_certificate_hash_or_adhoc(monkeypatch, tmp_path):
    from pathlib import Path

    from govorilka import installer, permissions

    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "app_bundle", lambda: Path("/Applications/Govorilka.app"))
    monkeypatch.setattr(installer, "designated_requirement", lambda app: 'designated => certificate leaf = H"AB12"')
    assert permissions.signing_identity() == "ab12"
    monkeypatch.setattr(installer, "designated_requirement", lambda app: 'designated => cdhash H"ff"')
    assert permissions.signing_identity().startswith("adhoc-")


# ---------- установка: понятная причина ----------


def test_running_from_the_dmg_says_why_install_is_unavailable(monkeypatch):
    from pathlib import PurePosixPath as Path

    from govorilka import installer

    monkeypatch.setattr(installer, "FROZEN", True)
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "app_bundle", lambda: Path("/Volumes/Govorilka/Govorilka.app"))
    assert not installer.can_install()
    assert "перетащите" in installer.mac_location_problem(installer.app_bundle())
