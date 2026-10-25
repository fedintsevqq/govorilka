"""Установка обновления в один клик (Windows).

Порядок: скачать новый файл рядом со старым, сверить размер и контрольную сумму SHA-256, которую публикует GitHub,
записать маленький скрипт и запустить его отдельно от программы. Скрипт ждёт, пока Говорилка закроется (запущенный
файл заменить нельзя), подменяет файл (старый остаётся как `.bak`, пока новая версия не запустится), запускает
новую версию и удаляет себя. Путь файла сохраняется: ярлыки и автозапуск продолжают работать.

Скачивание идёт только с github.com по HTTPS, а файл проверяется до запуска скрипта: повреждённый или подменённый
файл не будет установлен. Если контрольной суммы нет, установка отказывается и предлагает скачать вручную.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from govorilka import models, net
from govorilka.config import FROZEN
from govorilka.updates import UpdateInfo

log = logging.getLogger(__name__)

CHUNK = 256 * 1024
READ_TIMEOUT_S = 30.0
ALLOWED_HOSTS = ("github.com", ".githubusercontent.com")  # куда GitHub перенаправляет скачивание файлов релиза
UPDATED_FLAG = "--updated"
BACKUP_SUFFIX = ".bak"
SWAP_WAIT_S = 60  # сколько скрипт ждёт, пока старый файл освободится (антивирус тоже может держать его)
CREATE_NO_WINDOW, DETACHED_PROCESS, NEW_PROCESS_GROUP = 0x08000000, 0x00000008, 0x00000200


class InstallError(RuntimeError):
    """Установку остановили: текст показываем человеку, прежняя версия остаётся как была."""


def can_install() -> bool:
    """Установка в один клик работает у собранного приложения на Windows."""
    return sys.platform == "win32" and FROZEN


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(host == h.lstrip(".") or host.endswith(h) for h in ALLOWED_HOSTS):
        raise InstallError(f"Скачивание не с GitHub отклонено: {host or url}")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(
    url: str,
    target: Path,
    expected_size: int,
    on_status: Callable[[str], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Скачивает файл в target (сначала в target.part). Процент идёт в on_status в том виде, который понимает кольцо загрузки."""
    _check_url(url)
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Govorilka-updater"})
    done, last_report = 0, 0.0
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT_S, context=net.ssl_context()) as response:  # noqa: S310
            _check_url(response.geturl())  # после перенаправлений мы всё ещё на GitHub
            total = int(response.headers.get("Content-Length") or expected_size or 0)
            on_status(models.format_progress("обновление", 0, total or None))
            with part.open("wb") as out:
                while True:
                    if cancelled():
                        raise InstallError("Скачивание отменено")
                    block = response.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if time.monotonic() - last_report >= 0.4:
                        last_report = time.monotonic()
                        on_status(models.format_progress("обновление", done, total or None))
        part.replace(target)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise InstallError(f"Не удалось скачать файл: {exc}") from exc
    except InstallError:
        part.unlink(missing_ok=True)
        raise


def verify(path: Path, info: UpdateInfo) -> None:
    """Размер и SHA-256 должны совпасть с тем, что опубликовал GitHub, и это должна быть программа Windows."""
    if not info.sha256:
        raise InstallError("У релиза нет контрольной суммы: проверить файл нельзя. Скачайте его вручную")
    if info.size and path.stat().st_size != info.size:
        raise InstallError("Размер скачанного файла не совпал с релизом: скачивание оборвалось")
    with path.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise InstallError("Скачанный файл не похож на программу Windows")
    if sha256_of(path).lower() != info.sha256.lower():
        raise InstallError("Контрольная сумма не совпала: файл повреждён или подменён. Он не будет установлен")


def _command(parts: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part or "\\" in part else part for part in parts)


def swap_script(
    current: Path,
    new: Path,
    wait_pid: int,
    restart: list[str] | None = None,
    fallback: list[str] | None = None,
    max_wait: int = SWAP_WAIT_S,
) -> str:
    """Текст скрипта-подменителя (cmd). restart и fallback — чем запускать новую или прежнюю версию (в тестах подменяются
    безобидной командой); max_wait — сколько секунд пробовать подменить файл."""
    start = _command(restart or [str(current), UPDATED_FLAG])
    start_old = _command(fallback or [str(current)])
    backup = f"{current}{BACKUP_SUFFIX}"
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        "set PYINSTALLER_RESET_ENVIRONMENT=1",  # без этого новый onefile-процесс унаследовал бы папку старого
        f'set "TARGET={current}"',
        f'set "NEW={new}"',
        f'set "BACKUP={backup}"',
        "set /a tries=0",
        # 1. ждём, пока Говорилка закроется (строки без скобок: переменные раскрываются при выполнении каждой строки)
        ":wait",
        f'tasklist /FI "PID eq {wait_pid}" 2>nul | find " {wait_pid} " >nul',
        "if errorlevel 1 goto swap",
        "set /a tries+=1",
        f"if %tries% geq {max_wait} goto fail",
        "ping -n 2 127.0.0.1 >nul",  # пауза в 1 с: timeout без консоли падает сразу
        "goto wait",
        # 2. подменяем файл; пока его держит антивирус или закрывающийся процесс, пробуем снова
        ":swap",
        "set /a tries=0",
        ":again",
        'if exist "%BACKUP%" del /f /q "%BACKUP%" >nul 2>&1',
        'move /y "%TARGET%" "%BACKUP%" >nul 2>&1',
        "if errorlevel 1 goto retry",
        'move /y "%NEW%" "%TARGET%" >nul 2>&1',
        "if errorlevel 1 goto restore",
        # 3. запускаем новую версию и убираем за собой
        f'start "" {start}',
        'del /f /q "%NEW%" >nul 2>&1',
        '(goto) 2>nul & del /f /q "%~f0"',
        ":restore",
        'move /y "%BACKUP%" "%TARGET%" >nul 2>&1',
        ":retry",
        "set /a tries+=1",
        f"if %tries% geq {max_wait} goto fail",
        "ping -n 2 127.0.0.1 >nul",  # пауза в 1 с: timeout без консоли падает сразу
        "goto again",
        # не получилось подменить: оставляем как было и запускаем прежнюю версию
        ":fail",
        'if exist "%BACKUP%" if not exist "%TARGET%" move /y "%BACKUP%" "%TARGET%" >nul 2>&1',
        'del /f /q "%NEW%" >nul 2>&1',
        f'start "" {start_old}',
        '(goto) 2>nul & del /f /q "%~f0"',
        "",
    ])


def spawn_swap(current: Path, new: Path, restart: list[str] | None = None, **options: object) -> Path:
    """Пишет скрипт рядом с новым файлом и запускает его отдельно: он переживёт закрытие Говорилки."""
    script = new.with_name("govorilka-update.cmd")
    script.write_text(swap_script(current, new, os.getpid(), restart, **options), encoding="utf-8", newline="")
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen(  # noqa: S603 — скрипт мы только что написали сами
        ["cmd.exe", "/c", str(script)],
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | NEW_PROCESS_GROUP,
        close_fds=True,
        env=env,
        cwd=str(new.parent),
    )
    return script


def prepare(
    info: UpdateInfo,
    current: Path,
    on_status: Callable[[str], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Скачивает и проверяет новый файл рядом с current. Возвращает путь к нему; файл готов к подмене."""
    if not info.asset_url:
        raise InstallError("Для вашей системы в релизе нет файла")
    folder = current.parent
    try:
        probe = folder / ".govorilka-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise InstallError(f"В папку с программой нельзя записывать ({folder}). Скачайте файл вручную: {exc}") from exc
    new = current.with_name(f"{current.stem}.new{current.suffix}")
    new.unlink(missing_ok=True)
    download(info.asset_url, new, info.size, on_status, cancelled)
    on_status("Проверяю скачанный файл…")
    try:
        verify(new, info)
    except InstallError:
        new.unlink(missing_ok=True)
        raise
    return new


def remove_backup(current: Path) -> None:
    """Новая версия запустилась (флаг --updated): прежнюю копию можно удалить."""
    Path(f"{current}{BACKUP_SUFFIX}").unlink(missing_ok=True)
