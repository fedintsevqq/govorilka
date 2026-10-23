"""Настольные приложения нейросетей: ChatGPT и Claude на Windows и macOS.

Где приложение лежит, мы не угадываем: оно может стоять на любом диске и в любой папке. Спрашиваем у системы.
Windows: путь из регистрации ссылок `claude://` (там реальный путь, где бы ни стояла программа) и меню «Пуск»
(`Get-StartApps` знает и Microsoft Store, и обычные установки). Если и там нет — типичные папки на всех дисках.
macOS: стандартные папки, потом Spotlight по идентификатору приложения и Launch Services (`open -Ra`):
находят .app в любой папке, в том числе на внешнем диске.

Если приложение установлено, удобнее открыть разговор в нём: вы уже вошли в аккаунт, не нужен браузер.

- Claude: ссылки `claude://claude.ai/new?q=…` (чат) и `claude://code/new?q=…&folder=…` (Claude Code) открывают
  приложение с готовым текстом в поле ввода, отправка за вами. В `q` умещается около 14 000 знаков, но Windows
  не открывает ссылки длиннее ~2000, поэтому длинный разговор идёт через буфер обмена и автовставку.
- ChatGPT: у приложения нет ссылки для обычного чата, поэтому его запускают, а разговор вставляется сам (autopaste).
"""

from __future__ import annotations

import logging
import os
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MAX_LINK = 1800  # Windows (ShellExecute) отказывается от ссылок длиннее ~2000 знаков
FOUND_TTL_S = 3600.0
MISSING_TTL_S = 300.0  # «не нашли» помним недолго: человек мог установить приложение, пока Говорилка открыта


@dataclass(frozen=True)
class DesktopApp:
    key: str
    name: str
    mac_bundle: str  # имя .app
    mac_id: str  # идентификатор приложения в macOS: по нему находим .app где угодно
    windows_name: str  # как приложение называется в меню «Пуск»
    windows_exe: tuple[str, ...] = ()  # типичные пути от корня диска: запасной поиск
    scheme: str = ""  # ссылки вида scheme://…; пусто — приложение ссылок не понимает


APPS: dict[str, DesktopApp] = {
    "claude": DesktopApp(
        "claude", "Claude", "Claude", "com.anthropic.claudefordesktop", "Claude",
        ("Program Files\\Claude\\Claude.exe", "Program Files\\Anthropic\\Claude\\Claude.exe"), scheme="claude",
    ),
    "chatgpt": DesktopApp(
        "chatgpt", "ChatGPT", "ChatGPT", "com.openai.chat", "ChatGPT",
        ("Program Files\\ChatGPT\\ChatGPT.exe", "Program Files\\OpenAI\\ChatGPT\\ChatGPT.exe"),
    ),
}

_cache: dict[str, tuple[float, str | None]] = {}


def _mac_path(app: DesktopApp) -> str | None:
    """Путь к .app, где бы он ни лежал."""
    for base in (Path("/Applications"), Path.home() / "Applications"):
        candidate = base / f"{app.mac_bundle}.app"
        if candidate.exists():
            return str(candidate)
    try:  # внешний диск, другая папка, переименованный .app: система знает приложение по идентификатору
        lines = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{app.mac_id}'"], capture_output=True, text=True, timeout=8
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        lines = []
    for line in lines:
        if line.endswith(".app") and Path(line).exists():
            return line
    try:  # последняя проверка: Launch Services по имени (код возврата 0 — приложение нашлось)
        if subprocess.run(["open", "-Ra", app.mac_bundle], capture_output=True, timeout=8).returncode == 0:
            return app.mac_bundle
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _windows_scheme_registered(scheme: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command") as key:
            command = str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return False
    exe = command.split('"')[1] if command.startswith('"') else command.split(" ")[0]
    return os.path.exists(exe)


def _windows_app_id(app: DesktopApp) -> str | None:
    """Идентификатор приложения в меню «Пуск» (и Microsoft Store, и обычные программы, на любом диске)."""
    script = (
        f"Get-StartApps | Where-Object {{ $_.Name -eq '{app.windows_name}' }} "
        "| Select-Object -First 1 -ExpandProperty AppID"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]  # только Windows
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("Не получилось спросить меню «Пуск»: %s", exc)
        return None
    return result.stdout.strip() or None


def _windows_exe_on_any_drive(app: DesktopApp) -> str | None:
    """Типичные папки на каждом диске (C:, D:, E:…): программу часто ставят не на системный."""
    for letter in string.ascii_uppercase:
        for relative in app.windows_exe:
            candidate = f"{letter}:\\{relative}"
            if os.path.exists(candidate):
                return candidate
    return None


def find(key: str) -> str | None:
    """Чем открывать приложение: путь, схема ссылок или идентификатор в меню «Пуск». None — не установлено.
    Результат запоминается: поиск в системе небыстрый."""
    app = APPS.get(key)
    if app is None:
        return None
    cached = _cache.get(key)
    if cached is not None and time.monotonic() < cached[0]:
        return cached[1]
    found: str | None = None
    if sys.platform == "darwin":
        found = _mac_path(app)
    elif sys.platform == "win32":
        if app.scheme and _windows_scheme_registered(app.scheme):
            found = app.scheme
        else:
            found = _windows_app_id(app) or _windows_exe_on_any_drive(app)
    _cache[key] = (time.monotonic() + (FOUND_TTL_S if found else MISSING_TTL_S), found)
    return found


def forget() -> None:
    _cache.clear()


def installed(key: str) -> bool:
    return find(key) is not None


def open_link(url: str) -> None:
    """Открывает ссылку приложения (claude://…)."""
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 — ссылка собрана нами
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])


def launch(key: str) -> None:
    """Запускает приложение (если уже открыто — выводит вперёд)."""
    target = find(key)
    if target is None:
        raise OSError(f"{APPS[key].name} не установлен")
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", target])
    elif sys.platform == "win32":
        if target == APPS[key].scheme:  # приложение зарегистрировало схему: открываем «пустой» новый чат
            open_link(f"{target}://claude.ai/new")
        elif os.path.exists(target):  # нашли .exe на каком-то диске
            subprocess.Popen([target])
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
    else:
        raise OSError("Настольные приложения нейросетей поддерживаются на Windows и macOS")
