import base64
import shutil
import sys
from pathlib import Path, PurePosixPath

import numpy as np
import pytest

from govorilka import claude_bridge
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
    monkeypatch.setattr(claude_bridge, "bundled_base", lambda: tmp_path / "нет-такой-папки")  # Claude Desktop не ставили
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
    settings = load_settings(path)
    # настройки по умолчанию, но не как у нового пользователя: знакомство не показываем, автозапуск сам не включаем
    assert settings.model == Settings().model and settings.tour_done and settings.autostart_initialized
    backups = list(tmp_path.glob("s.json.bad-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{битый"  # испорченный файл не потерян


def test_settings_version_is_bumped_after_migration(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"settings_version": 1}', encoding="utf-8")
    from govorilka.config import SETTINGS_VERSION

    assert load_settings(path).settings_version == SETTINGS_VERSION


def test_failed_settings_save_is_reported(tmp_path, monkeypatch):
    from govorilka import config

    def denied(*_a, **_k):
        raise PermissionError("нет прав")

    monkeypatch.setattr("govorilka.transcript.write_atomic", denied)
    assert config.save_settings(Settings(), tmp_path / "s.json") is False


# ---------- Claude Code, встроенный в Claude Desktop ----------


def make_bundled(base: Path, version: str, *parts: str, name: str | None = None) -> Path:
    exe = base / version / Path(*parts) / (name or ("claude.exe" if sys.platform == "win32" else "claude"))
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")
    exe.chmod(0o755)
    return exe


def test_newest_bundled_version_wins_by_number_not_by_text(tmp_path):
    make_bundled(tmp_path, "2.9.5", "aaa")
    newest = make_bundled(tmp_path, "2.10.0", "bbb")  # по алфавиту «2.10» раньше «2.9», по номеру — новее
    make_bundled(tmp_path, "2.1.308", "ccc")
    assert claude_bridge.find_bundled_claude(tmp_path) == newest


@pytest.mark.parametrize("parts", [(), ("hash",), ("hash", "bin")])
def test_bundled_claude_found_in_any_known_layout(tmp_path, parts):
    exe = make_bundled(tmp_path, "2.1.308", *parts)
    assert claude_bridge.find_bundled_claude(tmp_path) == exe


def test_bundled_claude_missing_folder_or_empty_versions(tmp_path):
    assert claude_bridge.find_bundled_claude(tmp_path / "нет") is None
    (tmp_path / "2.1.308").mkdir()
    assert claude_bridge.find_bundled_claude(tmp_path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="на Windows права на запуск не проверяются")
def test_non_executable_file_is_not_picked_on_posix(tmp_path):
    exe = make_bundled(tmp_path, "2.1.308")
    exe.chmod(0o644)
    assert claude_bridge.find_bundled_claude(tmp_path) is None


def test_bundled_base_follows_the_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_bridge, "IS_MAC", True)
    assert claude_bridge.bundled_base() == Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"
    monkeypatch.setattr(claude_bridge, "IS_MAC", False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert claude_bridge.bundled_base() == tmp_path / "Claude" / "claude-code"


@pytest.mark.skipif(sys.platform == "darwin", reason="порядок поиска на Windows")
def test_own_install_beats_the_bundled_copy_and_bundled_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    bundled = make_bundled(tmp_path / "desktop", "2.1.308", "h")
    monkeypatch.setattr(claude_bridge, "bundled_base", lambda: tmp_path / "desktop")
    assert find_claude() == bundled  # своего нет — берём встроенный
    own = tmp_path / ".local" / "bin" / "claude.exe"
    own.parent.mkdir(parents=True)
    own.write_text("")
    assert find_claude() == own  # свой установленный важнее


def test_stream_resampler_keeps_length_and_has_no_seams():
    from govorilka.audio_base import StreamResampler

    rate, chunk = 48_000, 4_800
    t = np.arange(rate * 3) / rate
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    resampler = StreamResampler(rate)
    out = np.concatenate([resampler.process(tone[i : i + chunk]) for i in range(0, len(tone), chunk)])
    assert abs(len(out) - 16_000 * 3) < 800  # фильтр придерживает ~33 мс до следующего куска, остальное доходит
    ideal = 0.5 * np.sin(2 * np.pi * 440 * np.arange(len(out)) / 16_000)
    middle = slice(16_000, 32_000)  # первые сотни отсчётов — разгон фильтра
    delay = min(range(200), key=lambda d: float(np.abs(out[16_000 + d : 32_000 + d] - ideal[middle]).mean()))
    assert float(np.abs(out[16_000 + delay : 32_000 + delay] - ideal[middle]).max()) < 1e-3  # раньше на стыках было 0.0078


def test_stream_resampler_passes_16k_through():
    from govorilka.audio_base import StreamResampler

    data = np.linspace(-1, 1, 1600, dtype=np.float32)
    assert np.array_equal(StreamResampler(16_000).process(data), data)
