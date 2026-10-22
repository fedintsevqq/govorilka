"""Открывает Claude Code в новом окне терминала и передаёт ему транскрипт."""

from __future__ import annotations

import base64
import os
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
    if IS_MAC:
        return _find_claude_mac()
    native = Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe"
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


def _find_claude_mac() -> Path | None:
    for place in MAC_CLAUDE_PLACES:
        if place.is_file():
            return place
    found = shutil.which("claude")
    return Path(found) if found else None


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
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise OSError(f"Terminal не открылся: {result.stderr.strip()}")
        return
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoExit",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encode_command(build_command(claude, workdir, prompt)),
        ],
        creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]  # есть только на Windows
    )
