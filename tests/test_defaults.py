import json
import sys
from pathlib import Path

import pytest

from govorilka import config
from govorilka.config import APP_DIR, Settings, downloads_dir, load_settings
from govorilka.echo import is_echo
from govorilka.transcript import Utterance


def test_downloads_dir_is_the_real_system_folder():
    folder = downloads_dir()
    assert folder.name.lower() in {"downloads", "загрузки"}
    assert folder.parent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="запрос к Windows")
def test_downloads_dir_follows_windows_even_when_moved():
    import ctypes

    # папку могли перенести на другой диск: берём то, что говорит система, а не ~/Downloads
    assert downloads_dir() == downloads_dir()
    assert ctypes.windll.shell32 is not None


def test_transcripts_go_to_downloads_by_default():
    assert Path(Settings().transcripts_dir) == downloads_dir() / "Govorilka"


@pytest.mark.parametrize("old", [str(Path.home() / "Documents" / "Govorilka"), str(APP_DIR / "transcripts")])
def test_old_default_folder_moves_to_downloads_but_a_chosen_one_stays(tmp_path, old):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"transcripts_dir": old, "capture_mic": False}), encoding="utf-8")
    loaded = load_settings(path)
    assert Path(loaded.transcripts_dir) == downloads_dir() / "Govorilka"
    assert loaded.capture_mic is False  # остальное сохранилось
    chosen = tmp_path / "s2.json"
    chosen.write_text(json.dumps({"transcripts_dir": "D:\Мои созвоны"}), encoding="utf-8")
    assert load_settings(chosen).transcripts_dir == "D:\Мои созвоны"


def test_folder_chosen_after_migration_is_kept(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps({"transcripts_dir": str(Path.home() / "Documents" / "Govorilka"), "settings_version": 2}),
        encoding="utf-8",
    )
    assert load_settings(path).transcripts_dir == str(Path.home() / "Documents" / "Govorilka")


def test_hotkey_defaults_per_platform():
    expected = "alt+cmd" if sys.platform == "darwin" else "ctrl+shift"
    assert Settings().hotkey == expected and Settings().hotkey_enabled is True
    assert config.SETTINGS_VERSION == 2


def test_short_reply_is_not_mistaken_for_speaker_echo():
    others = [Utterance("Собеседник 1", 10, "Ну да, давайте так и сделаем", 13)]
    assert not is_echo(Utterance("Я", 12, "Да.", 12.5), others)
    assert is_echo(Utterance("Я", 10.5, "давайте так и сделаем", 13), others)
