"""Фаза 1: исправления горячей клавиши, автозапуска и запуска приложения."""

import sys
import time

import pytest

from govorilka import autostart
from govorilka.hotkeys import (
    HotkeyWatcher,
    KeySnapshot,
    conflicts_with_language_switch,
    format_snapshot,
    input_monitoring_status,
    parse_chord,
)


def snap(mods="", keys=""):
    return KeySnapshot(frozenset(m for m in mods.split("+") if m), frozenset(k for k in keys.split("+") if k))


class HeldKeyboard:
    def __init__(self, held):
        self.held = held

    def snapshot(self):
        return self.held


def wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---------- горячая клавиша молчит, пока в настройках записывают новую ----------


def test_paused_watcher_does_not_fire_while_recording_a_new_chord():
    fired = []
    keyboard = HeldKeyboard(snap("alt+cmd"))  # пользователь зажал старую комбинацию, собирая новую
    watcher = HotkeyWatcher(lambda: fired.append(1), keyboard=keyboard)
    watcher.set_chord(parse_chord("alt+cmd"))
    watcher.pause()
    watcher.start()
    time.sleep(0.8)  # дольше, чем нужно для срабатывания (0.5 с)
    assert fired == []
    keyboard.held = snap()  # отпустил всё и сохранил настройки
    watcher.resume()
    time.sleep(0.2)
    assert fired == []  # после паузы старое удержание не засчитывается
    keyboard.held = snap("alt+cmd")
    assert wait_for(lambda: fired == [1])  # а новое — да
    watcher.stop()


# ---------- конфликт с переключением языка ----------


def test_conflict_with_language_switch():
    ctrl_shift = frozenset({"ctrl", "shift"})
    assert conflicts_with_language_switch(parse_chord("ctrl+shift"), ctrl_shift)
    assert not conflicts_with_language_switch(parse_chord("ctrl+shift+r"), ctrl_shift)  # с буквой — не мешает
    assert not conflicts_with_language_switch(parse_chord("ctrl+shift"), frozenset({"alt", "shift"}))
    assert not conflicts_with_language_switch(parse_chord("ctrl+shift"), None)


# ---------- живая подсказка «что нажато» ----------


def test_live_key_text():
    assert format_snapshot(snap()) == "ничего"
    assert format_snapshot(snap("alt+cmd", "r"), mac=True) == "Option + Command + R"
    assert format_snapshot(snap("ctrl", "vk27"), mac=False) == "Ctrl + клавиша 27"


@pytest.mark.skipif(sys.platform == "darwin", reason="на Mac ответ зависит от разрешения")
def test_input_monitoring_is_a_mac_only_question():
    assert input_monitoring_status() is None


# ---------- автозапуск чинит себя после обновления ----------


def test_mac_autostart_points_to_the_new_app_after_update(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")  # проверяем ветку Mac на любой ОС
    agent = tmp_path / "agent.plist"
    old = ["/Users/v/Downloads/Govorilka-0.2.0.app/Contents/MacOS/Govorilka", "--background"]
    new = ["/Applications/Govorilka.app/Contents/MacOS/Govorilka", "--background"]
    autostart._set_mac(True, old, agent)
    assert autostart.refresh(new, agent_path=agent) is True
    assert "/Applications/Govorilka.app" in autostart.registered_command(agent_path=agent)
    assert autostart.refresh(new, agent_path=agent) is False  # уже верно — не трогаем


def test_disabled_autostart_is_not_turned_on_by_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    agent = tmp_path / "agent.plist"
    assert autostart.refresh(["/app", "--background"], agent_path=agent) is False
    assert not agent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="реестр Windows")
def test_windows_autostart_points_to_the_new_exe_after_update():
    import winreg

    key = r"Software\GovorilkaTest\Run"
    old = [r"E:\Downloads\Govorilka-0.2.0-windows-x64.exe", "--background"]
    new = [r"E:\Downloads\Govorilka-0.2.1-windows-x64.exe", "--background"]
    try:
        autostart.set_enabled(True, old, run_key=key)
        assert autostart.refresh(new, run_key=key) is True
        assert "0.2.1" in autostart.registered_command(run_key=key)
        assert autostart.refresh(new, run_key=key) is False
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\GovorilkaTest")
        except OSError:
            pass


# ---------- самопроверка собранного приложения ----------


def test_selftest_requires_the_expected_word(monkeypatch):
    from govorilka import selftest

    monkeypatch.setattr(selftest.sys, "platform", "win32")  # помощник Mac здесь не проверяем
    monkeypatch.setenv("GOVORILKA_SELFTEST_EXPECT", "коллеги")
    assert selftest._problems("[Спикер 1] Добрый день, Коллеги.") == []
    assert selftest._problems("[Спикер 1] бессмыслица") == ["в расшифровке нет ожидаемого слова «коллеги»"]


def test_selftest_on_mac_checks_the_audio_helper(monkeypatch, tmp_path):
    from govorilka import selftest

    monkeypatch.setattr(selftest.sys, "platform", "darwin")
    monkeypatch.delenv("GOVORILKA_SELFTEST_EXPECT", raising=False)
    monkeypatch.setattr(selftest, "AUDIOTAP_PATH", tmp_path / "нет-помощника")
    assert "помощник записи звука" in selftest._problems("текст")[0]


def test_old_version_on_the_legacy_port_is_noticed():
    import socket

    from govorilka import single_instance

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert single_instance.legacy_instance_suspected(free_port) is False
    with socket.socket() as old_version:
        old_version.bind(("127.0.0.1", 0))
        old_version.listen(1)
        assert single_instance.legacy_instance_suspected(old_version.getsockname()[1]) is True


def test_ui_selftest_passes_on_the_source_tree(tmp_path, monkeypatch, qapp):
    """Та же проверка, что CI гоняет в собранном приложении, — здесь на исходниках."""
    from govorilka import selftest

    monkeypatch.setattr(selftest, "DATA_DIR", tmp_path)
    assert selftest.run_ui() == 0, (tmp_path / "selftest-ui.txt").read_text(encoding="utf-8")
    assert (tmp_path / "selftest-ui.txt").read_text(encoding="utf-8").startswith("OK")
