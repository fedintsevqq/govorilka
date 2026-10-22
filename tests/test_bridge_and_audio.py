import base64
import shutil
import sys
from pathlib import Path, PurePosixPath

import numpy as np
import pytest

from govorilka.audio import to_mono_16k
from govorilka.audio_base import resample_mono
from govorilka.claude_bridge import (
    build_applescript,
    build_command,
    build_prompt,
    build_shell_command,
    encode_command,
    find_claude,
)
from govorilka.config import Settings, load_settings, save_settings


def test_build_command_escapes_single_quotes():
    cmd = build_command(Path(r"C:\bin\claude.exe"), Path(r"C:\Мои записи"), "Прочитай 'файл' и сделай резюме")
    assert cmd == (
        "Set-Location -LiteralPath 'C:\\Мои записи'; "
        "& 'C:\\bin\\claude.exe' 'Прочитай ''файл'' и сделай резюме'"
    )


def test_typographic_quotes_cannot_break_out():
    cmd = build_command(Path("c.exe"), Path("d"), "Bob’s; calc")
    assert cmd.endswith("'Bob’’s; calc'")


def test_build_prompt_inserts_file_or_appends_it():
    f = Path(r"C:\t\call.md")
    assert build_prompt(f, "Резюме: {file}") == r"Резюме: C:\t\call.md"
    assert build_prompt(f, "Резюме") == "Резюме\n\nФайл: C:\\t\\call.md"


def test_mac_shell_command_and_applescript_escaping():
    cmd = build_shell_command(
        PurePosixPath("/Users/v/.local/bin/claude"), PurePosixPath("/Users/v/Мои записи"), 'Резюме "созвона" \\ и \'это\''
    )
    assert cmd.startswith("cd '/Users/v/Мои записи' && /Users/v/.local/bin/claude ")
    script = build_applescript(cmd)
    # внутри AppleScript-строки кавычки и обратные слэши экранированы
    assert '\\"созвона\\"' in script and "\\\\" in script
    assert script.startswith('tell application "Terminal"')


def test_resample_mono_48k_to_16k():
    out = resample_mono(np.ones(4800, dtype=np.float32), 48_000)
    assert out.dtype == np.float32 and len(out) == 1600


@pytest.mark.skipif(sys.platform == "darwin", reason="на Mac поиск идёт по другим путям")
def test_dead_npm_shim_is_ignored(tmp_path, monkeypatch):
    shim = tmp_path / "claude.ps1"
    shim.write_text("")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: str(shim) if name == "claude.ps1" else None)
    assert find_claude() is None
    (tmp_path / "node_modules" / "@anthropic-ai" / "claude-code").mkdir(parents=True)
    assert find_claude() == shim


def test_encode_command_is_utf16_base64():
    assert base64.b64decode(encode_command("привет")).decode("utf-16-le") == "привет"


def test_to_mono_16k_downmixes_and_resamples():
    stereo = np.zeros((4800, 2), dtype=np.int16)
    stereo[:, 0] = 1000
    stereo[:, 1] = 3000
    out = to_mono_16k(stereo.tobytes(), channels=2, rate=48_000)
    assert out.dtype == np.float32
    assert len(out) == 1600
    assert abs(float(np.median(out)) - 2000 / 32768) < 1e-3


def test_settings_roundtrip_and_bad_model(tmp_path):
    path = tmp_path / "s.json"
    save_settings(Settings(capture_mic=False, model="нет-такой"), path)
    loaded = load_settings(path)
    assert loaded.capture_mic is False
    assert loaded.model == Settings().model


def test_broken_settings_file_falls_back(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{битый", encoding="utf-8")
    assert load_settings(path) == Settings()
