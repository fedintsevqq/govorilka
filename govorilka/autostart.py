"""Запуск Говорилки при входе в систему.

Windows: значение в HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
macOS: LaunchAgent в ~/Library/LaunchAgents. Оба раза приложение стартует с --background,
то есть свёрнутым: окно не выскакивает, работает горячая клавиша.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
from pathlib import Path

from govorilka.config import BUNDLE_ID, FROZEN

log = logging.getLogger(__name__)

APP_NAME = "Govorilka"
LAUNCH_AGENT_ID = BUNDLE_ID
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKGROUND_FLAG = "--background"


def launch_command() -> list[str]:
    """Чем запускать при входе. Из собранного приложения — оно само, из исходников — python -m govorilka."""
    if FROZEN:
        return [sys.executable, BACKGROUND_FLAG]
    executable = sys.executable
    if sys.platform == "win32" and executable.lower().endswith("python.exe"):
        windowless = executable[: -len("python.exe")] + "pythonw.exe"  # без чёрного окна консоли
        if Path(windowless).exists():
            executable = windowless
    return [executable, "-m", "govorilka", BACKGROUND_FLAG]


def unstable_location(executable: str) -> bool:
    """Приложение запущено прямо из образа диска или из временной копии macOS (App Translocation): после
    перезагрузки этого пути не будет, и автозапуск сломался бы молча."""
    return "/AppTranslocation/" in executable or executable.startswith("/Volumes/")


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_ID}.plist"


def launch_agent_plist(command: list[str]) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCH_AGENT_ID,
            "ProgramArguments": command,
            "RunAtLoad": True,
            "ProcessType": "Interactive",
            # «Объекты входа» показывают запись как «Говорилка» с её значком, а не как программу неизвестного
            # разработчика; удалили программу — macOS понимает, чья это запись
            "AssociatedBundleIdentifiers": [BUNDLE_ID],
        }
    )


def is_enabled(run_key: str = WINDOWS_RUN_KEY, agent_path: Path | None = None) -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except FileNotFoundError:
            return False
    if sys.platform == "darwin":
        return (agent_path or launch_agent_path()).exists()
    return False


def registered_command(run_key: str = WINDOWS_RUN_KEY, agent_path: Path | None = None) -> str | None:
    """Что сейчас записано в автозапуске (строкой). None — автозапуск выключен."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
        except FileNotFoundError:
            return None
    if sys.platform == "darwin":
        path = agent_path or launch_agent_path()
        try:
            arguments = plistlib.loads(path.read_bytes()).get("ProgramArguments", [])
        except (OSError, plistlib.InvalidFileException):
            return None
        return subprocess.list2cmdline([str(a) for a in arguments])
    return None


def refresh(
    command: list[str] | None = None, run_key: str = WINDOWS_RUN_KEY, agent_path: Path | None = None
) -> bool:
    """Если автозапуск включён, но указывает на старое место (обновили или перенесли программу), чиним.

    Файлы релизов называются по версии (Govorilka-0.2.1-…exe), поэтому после каждого обновления
    прежняя запись вела бы на удалённый файл. True — запись поправили.
    """
    command = command or launch_command()
    current = registered_command(run_key, agent_path)
    if current is None or current == subprocess.list2cmdline(command):
        return False
    set_enabled(True, command, run_key, agent_path)
    log.info("Автозапуск указывал на %s, теперь на %s", current, command)
    return True


def set_enabled(
    enabled: bool,
    command: list[str] | None = None,
    run_key: str = WINDOWS_RUN_KEY,
    agent_path: Path | None = None,
) -> None:
    """Включает или выключает автозапуск. OSError, если система не дала записать."""
    command = command or launch_command()
    if sys.platform == "win32":
        _set_windows(enabled, command, run_key)
    elif sys.platform == "darwin":
        if enabled and unstable_location(command[0]):
            raise OSError("Перетащите Говорилку в «Программы» и запустите оттуда: из образа диска или временной копии автозапуск после перезагрузки не найдёт программу")
        _set_mac(enabled, command, agent_path or launch_agent_path())
    else:
        raise OSError("Автозапуск поддерживается только на Windows и macOS")


def _set_windows(enabled: bool, command: list[str], run_key: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            return
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass  # и так выключено


def _set_mac(enabled: bool, command: list[str], path: Path) -> None:
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(launch_agent_plist(command))
    else:
        path.unlink(missing_ok=True)
