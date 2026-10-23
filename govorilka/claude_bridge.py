"""Открывает Claude Code в новом окне терминала и передаёт ему транскрипт."""

from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
INSTALL_COMMAND = (
    "curl -fsSL https://claude.ai/install.sh | bash" if IS_MAC else "irm https://claude.ai/install.ps1 | iex"
)
INSTALL_HINT = (
    f"Claude Code (CLI) не найден. Установите его одной командой в {'Терминале' if IS_MAC else 'PowerShell'}:\n"
    f"{INSTALL_COMMAND}\n\n"
    "Запрос с путём к транскрипту я скопировал в буфер обмена — его можно вставить в Claude вручную."
)
# приложение из Finder получает урезанный PATH, поэтому смотрим и типичные места установки
MAC_CLAUDE_PLACES = (
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
    Path.home() / ".npm-global" / "bin" / "claude",
)


class ClaudeNotFoundError(RuntimeError):
    pass


def _npm_shim_is_alive(shim: Path) -> bool:
    # от удалённого npm-пакета остаётся claude.ps1, который ведёт в никуда
    return (shim.parent / "node_modules" / "@anthropic-ai" / "claude-code").is_dir()


def find_claude() -> Path | None:
    """Ищет Claude Code: свой установщик, потом PATH, потом копия, встроенная в Claude Desktop."""
    standalone = _find_standalone_mac() if IS_MAC else _find_standalone_windows()
    return standalone or find_bundled_claude()


def _find_standalone_windows() -> Path | None:
    native = Path.home() / ".local" / "bin" / "claude.exe"
    if native.is_file():
        return native
    for name in ("claude.exe", "claude.cmd", "claude.ps1", "claude"):
        found = shutil.which(name)
        if not found:
            continue
        path = Path(found)
        if path.suffix.lower() in (".ps1", ".cmd") and not _npm_shim_is_alive(path):
            continue
        return path
    return None


def _find_standalone_mac() -> Path | None:
    for place in MAC_CLAUDE_PLACES:
        if place.is_file():
            return place
    found = shutil.which("claude")
    return Path(found) if found else None


# ---------- Claude Code, встроенный в приложение Claude Desktop ----------

_VERSION = re.compile(r"\d+(?:\.\d+)*")
_BUNDLED_NAMES = ("claude.exe", "claude")
_BUNDLED_DEPTH = 3  # claude-code/<версия>/[<хеш>/[папка/]]claude


def bundled_base() -> Path:
    """Где Claude Desktop хранит свои копии Claude Code (обновляет их сам, версия в имени папки)."""
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Claude" / "claude-code"


def _version_key(version_dir: str) -> tuple[int, ...]:
    match = _VERSION.search(version_dir)
    return tuple(int(part) for part in match.group().split(".")) if match else (0,)


def _is_runnable(path: Path) -> bool:
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def find_bundled_claude(base: Path | None = None) -> Path | None:
    """Самая свежая встроенная копия. Версии сравниваем по числам: 2.10.0 новее 2.9.5, а не наоборот."""
    base = base or bundled_base()
    try:
        version_dirs = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return None  # Claude Desktop не установлен или не запускался
    best: tuple[tuple[int, ...], float, Path] | None = None
    for version_dir in version_dirs:
        key = _version_key(version_dir.name)
        for name in _BUNDLED_NAMES:
            for depth in range(_BUNDLED_DEPTH):
                for path in version_dir.glob("/".join(["*"] * depth + [name])):
                    if _is_runnable(path):
                        candidate = (key, path.stat().st_mtime, path)
                        if best is None or candidate[:2] > best[:2]:
                            best = candidate
    return best[2] if best else None


def build_prompt(transcript_file: Path, template: str) -> str:
    if "{file}" in template:
        return template.replace("{file}", str(transcript_file))
    return f"{template}\n\nФайл: {transcript_file}"


# PowerShell считает одинарной кавычкой и типографские ‘ ’ ‚ ‛
_PS_SINGLE_QUOTES = "'‘’‚‛"


def _ps_quote(value: str) -> str:
    """Строка в одинарных кавычках PowerShell: каждая кавычка внутри удваивается."""
    escaped = "".join(ch * 2 if ch in _PS_SINGLE_QUOTES else ch for ch in value)
    # PowerShell 5.1 съедает двойные кавычки в аргументах внешних программ
    return "'" + escaped.replace('"', "«") + "'"


def build_command(claude: Path, workdir: Path, prompt: str) -> str:
    return f"Set-Location -LiteralPath {_ps_quote(str(workdir))}; & {_ps_quote(str(claude))} {_ps_quote(prompt)}"


def encode_command(command: str) -> str:
    # -EncodedCommand ждёт base64 от UTF-16LE: так кириллица и кавычки доходят без искажений
    return base64.b64encode(command.encode("utf-16-le")).decode("ascii")


def build_shell_command(claude: Path, workdir: Path, prompt: str) -> str:
    return f"cd {shlex.quote(str(workdir))} && {shlex.quote(str(claude))} {shlex.quote(prompt)}"


def build_applescript(shell_command: str) -> str:
    """Скрипт для Terminal.app: открыть новое окно и выполнить команду."""
    escaped = shell_command.replace("\\", "\\\\").replace('"', '\\"')
    return f'tell application "Terminal"\n  activate\n  do script "{escaped}"\nend tell'


def open_in_claude(transcript_file: Path, workdir: Path, prompt: str) -> None:
    claude = find_claude()
    if claude is None:
        raise ClaudeNotFoundError(INSTALL_HINT)
    workdir.mkdir(parents=True, exist_ok=True)
    if IS_MAC:
        script = build_applescript(build_shell_command(claude, workdir, prompt))
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30
            )
        except subprocess.TimeoutExpired as exc:
            # первый запуск: macOS спрашивает, можно ли Говорилке управлять Терминалом, и ждёт ответа
            raise OSError("Терминал не ответил за 30 секунд. Разрешите Говорилке управлять Терминалом и повторите.") from exc
        if result.returncode != 0:
            raise OSError(f"Terminal не открылся: {result.stderr.strip()}")
        return
    shell = [
        "powershell.exe",
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encode_command(build_command(claude, workdir, prompt)),
    ]
    # Claude Code — консольная программа, ей нужен терминал. Современный Windows Terminal (вкладки, нормальные шрифты
    # и цвета) берём, если он есть; иначе — классическое окно PowerShell.
    terminal = shutil.which("wt.exe")
    if terminal:
        subprocess.Popen([terminal, "--title", "Claude Code", "-d", str(workdir), *shell])
        return
    subprocess.Popen(shell, creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]  # только Windows
