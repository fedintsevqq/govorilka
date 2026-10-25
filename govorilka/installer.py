"""Установка обновления в один клик (Windows и macOS).

Порядок: скачать новый файл рядом со старым, сверить размер и контрольную сумму SHA-256, которую публикует GitHub,
записать маленький скрипт и запустить его отдельно от программы. Скрипт ждёт, пока Говорилка закроется (запущенный
файл заменить нельзя), подменяет файл (старый остаётся как `.bak`, пока новая версия не запустится), запускает
новую версию и удаляет себя. Путь файла сохраняется: ярлыки и автозапуск продолжают работать.

Скачивание идёт только с github.com по HTTPS, а файл проверяется до запуска скрипта: повреждённый или подменённый
файл не будет установлен. Если контрольной суммы нет, установка отказывается и предлагает скачать вручную.

На Mac скачивается `.dmg`, сверяется SHA-256, образ монтируется только на чтение, а из него копируется `Говорилка.app`
в соседнюю скрытую папку. Подпись новой программы должна быть целой и совпадать с подписью установленной (тот же
сертификат): иначе macOS сбросила бы выданные разрешения, а чужую подпись мы не примем. Затем shell-скрипт ждёт закрытия
программы, меняет `.app` местами (прежняя остаётся скрытой копией) и открывает новую версию.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
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
# Системные команды по полному пути: в PATH бывает find из Git, который ищет файлы, а не строки.
TASKLIST, FIND = r"%SystemRoot%\System32\tasklist.exe", r"%SystemRoot%\System32\find.exe"
PAUSE = r'"%SystemRoot%\System32\ping.exe" -n 2 127.0.0.1 >nul'  # пауза в 1 с: timeout без консоли сразу падает
CREATE_NO_WINDOW, DETACHED_PROCESS, NEW_PROCESS_GROUP = 0x08000000, 0x00000008, 0x00000200


class InstallError(RuntimeError):
    """Установку остановили: текст показываем человеку, прежняя версия остаётся как была."""


def app_bundle(executable: Path | None = None) -> Path | None:
    """`.app`, внутри которого запущена программа (Mac)."""
    for parent in (executable or Path(sys.executable)).parents:
        if parent.suffix == ".app":
            return parent
    return None


def mac_location_problem(bundle: Path | None) -> str:
    """Почему на Mac нельзя заменить программу прямо на месте ('' — можно)."""
    if bundle is None:
        return "Программа запущена не из .app"
    text = str(bundle)
    if "/AppTranslocation/" in text or text.startswith("/Volumes/"):
        return "Говорилка запущена прямо из образа или карантина: перетащите её в «Программы» и откройте оттуда"
    if not os.access(bundle.parent, os.W_OK):
        return f"В папку {bundle.parent} нельзя записывать"
    return ""


def can_install() -> bool:
    """Установка в один клик работает у собранного приложения: на Windows и на Mac (из «Программ» или другой своей папки)."""
    if not FROZEN:
        return False
    if sys.platform == "win32":
        return True
    return sys.platform == "darwin" and not mac_location_problem(app_bundle())


def _check_url(url: str, allowed: tuple[str, ...] = ALLOWED_HOSTS) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(host == h.lstrip(".") or host.endswith(h) for h in allowed):
        raise InstallError(f"Скачивание с этого адреса отклонено: {host or url}")


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
    label: str = "обновление",
    allowed: tuple[str, ...] = ALLOWED_HOSTS,
) -> None:
    """Скачивает файл в target (сначала в target.part). Процент идёт в on_status в том виде, который понимает кольцо загрузки."""
    _check_url(url, allowed)
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Govorilka-updater"})
    done, last_report = 0, 0.0
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT_S, context=net.ssl_context()) as response:  # noqa: S310
            _check_url(response.geturl(), allowed)  # после перенаправлений мы всё ещё там, где разрешено
            total = int(response.headers.get("Content-Length") or expected_size or 0)
            on_status(models.format_progress(label, 0, total or None))
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
                        on_status(models.format_progress(label, done, total or None))
        part.replace(target)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise InstallError(f"Не удалось скачать файл: {exc}") from exc
    except InstallError:
        part.unlink(missing_ok=True)
        raise


def verify(path: Path, info: UpdateInfo, windows_exe: bool = True) -> None:
    """Размер и SHA-256 должны совпасть с тем, что опубликовал GitHub; для Windows это ещё и программа (MZ)."""
    if not info.sha256:
        raise InstallError("У релиза нет контрольной суммы: проверить файл нельзя. Скачайте его вручную")
    if info.size and path.stat().st_size != info.size:
        raise InstallError("Размер скачанного файла не совпал с релизом: скачивание оборвалось")
    if windows_exe:
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
        f'"{TASKLIST}" /FI "PID eq {wait_pid}" 2>nul | "{FIND}" " {wait_pid} " >nul',
        "if errorlevel 1 goto swap",
        "set /a tries+=1",
        f"if %tries% geq {max_wait} goto fail",
        PAUSE,
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
        PAUSE,
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


# ---------- macOS ----------

LEAF_HASH = re.compile(r'certificate leaf = H"([0-9a-fA-F]+)"')
MAC_NEW_NAME, MAC_BACKUP_NAME = ".{stem}.new.app", ".{stem}.old.app"
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(command: list[str], timeout: float = 300) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603


def leaf_hash(requirement_text: str) -> str:
    """Отпечаток сертификата из строки `designated => … certificate leaf = H"…"` ('' — программа без такого условия)."""
    match = LEAF_HASH.search(requirement_text)
    return match.group(1).lower() if match else ""


def designated_requirement(app: Path, run: Runner = _run) -> str:
    result = run(["codesign", "-d", "-r-", str(app)])
    return f"{result.stdout}\n{result.stderr}"


def verify_app_signature(new_app: Path, current_app: Path, run: Runner = _run) -> None:
    """Подпись новой программы цела и сделана тем же сертификатом, что у установленной."""
    checked = run(["codesign", "--verify", "--deep", "--strict", str(new_app)])
    if checked.returncode != 0:
        raise InstallError("Подпись новой версии повреждена или отсутствует. Она не будет установлена")
    wanted = leaf_hash(designated_requirement(current_app, run))
    if not wanted:  # установленная версия подписана без постоянного сертификата: сверять нечего, остаётся SHA-256
        return
    if leaf_hash(designated_requirement(new_app, run)) != wanted:
        raise InstallError("Новая версия подписана другим сертификатом. Она не будет установлена")


def find_app(folder: Path) -> Path:
    apps = sorted(p for p in folder.iterdir() if p.suffix == ".app" and p.is_dir())
    if not apps:
        raise InstallError("В скачанном образе нет программы")
    return apps[0]


def stage_from_dmg(dmg: Path, current_app: Path, staged: Path, run: Runner = _run) -> None:
    """Монтирует образ только на чтение, проверяет подпись и копирует программу в staged."""
    mount = Path(tempfile.mkdtemp(prefix="govorilka-mnt-"))
    attached = False
    try:
        result = run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-noautoopen", "-mountpoint", str(mount), str(dmg)])
        if result.returncode != 0:
            raise InstallError(f"Не удалось открыть образ: {(result.stderr or result.stdout).strip()[:200]}")
        attached = True
        source = find_app(mount)
        verify_app_signature(source, current_app, run)
        shutil.rmtree(staged, ignore_errors=True)
        copied = run(["ditto", str(source), str(staged)])
        if copied.returncode != 0:
            raise InstallError(f"Не удалось скопировать программу: {copied.stderr.strip()[:200]}")
        verify_app_signature(staged, current_app, run)  # уже копия: ту же проверку, что увидит macOS при запуске
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    finally:
        if attached:
            run(["hdiutil", "detach", "-force", str(mount)])
        shutil.rmtree(mount, ignore_errors=True)


def prepare_mac(
    info: UpdateInfo,
    current_app: Path,
    on_status: Callable[[str], None],
    cancelled: Callable[[], bool] = lambda: False,
    run: Runner = _run,
) -> Path:
    """Скачивает и проверяет образ, кладёт новую программу рядом с текущей. Возвращает путь к ней."""
    if not info.asset_url:
        raise InstallError("Для вашей системы в релизе нет файла")
    problem = mac_location_problem(current_app)
    if problem:
        raise InstallError(problem)
    staged = current_app.with_name(MAC_NEW_NAME.format(stem=current_app.stem))
    work = Path(tempfile.mkdtemp(prefix="govorilka-update-"))
    try:
        dmg = work / "update.dmg"
        download(info.asset_url, dmg, info.size, on_status, cancelled)
        on_status("Проверяю скачанный файл…")
        verify(dmg, info, windows_exe=False)
        stage_from_dmg(dmg, current_app, staged, run)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return staged


def mac_swap_script(current: Path, staged: Path, wait_pid: int, max_wait: int = SWAP_WAIT_S, restart: str | None = None) -> str:
    """Скрипт sh: ждёт закрытия Говорилки, меняет .app местами, открывает новую версию. restart — команда запуска (в тестах подменяется)."""
    backup = current.with_name(MAC_BACKUP_NAME.format(stem=current.stem))
    q = shlex.quote
    start = restart or f"open {q(str(current))} --args {UPDATED_FLAG}"
    return "\n".join([
        "#!/bin/sh",
        f"TARGET={q(str(current))}",
        f"NEW={q(str(staged))}",
        f"BACKUP={q(str(backup))}",
        'SELF="$0"',
        "tries=0",
        "fail() {",
        '  if [ -e "$BACKUP" ] && [ ! -e "$TARGET" ]; then mv "$BACKUP" "$TARGET"; fi',
        '  rm -rf "$NEW"',
        '  open "$TARGET"',
        '  rm -f "$SELF"',
        "  exit 0",
        "}",
        f'while kill -0 {wait_pid} 2>/dev/null; do',
        "  tries=$((tries+1))",
        f'  if [ "$tries" -ge {max_wait} ]; then fail; fi',
        "  sleep 1",
        "done",
        "tries=0",
        'rm -rf "$BACKUP"',
        'until mv "$TARGET" "$BACKUP" 2>/dev/null; do',
        "  tries=$((tries+1))",
        f'  if [ "$tries" -ge {max_wait} ]; then fail; fi',
        "  sleep 1",
        "done",
        'if ! mv "$NEW" "$TARGET"; then fail; fi',
        'xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null',
        start,
        'rm -f "$SELF"',
        "",
    ])


def spawn_swap_mac(current: Path, staged: Path, **options: object) -> Path:
    """Пишет скрипт рядом со скрытой копией и запускает его отдельно от программы."""
    script = staged.with_name(".govorilka-update.sh")
    script.write_text(mac_swap_script(current, staged, os.getpid(), **options), encoding="utf-8")  # type: ignore[arg-type]
    script.chmod(0o700)
    subprocess.Popen(  # noqa: S603 — скрипт мы только что написали сами
        ["/bin/sh", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return script


def mac_backup(current: Path) -> Path:
    return current.with_name(MAC_BACKUP_NAME.format(stem=current.stem))


# ---------- одна точка входа ----------


def install(info: UpdateInfo, on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> None:
    """Скачивает, проверяет и запускает подмену. После возврата программа должна закрыться."""
    if sys.platform == "darwin":
        bundle = app_bundle()
        if bundle is None:
            raise InstallError("Программа запущена не из .app")
        spawn_swap_mac(bundle, prepare_mac(info, bundle, on_status, cancelled))
        return
    current = Path(sys.executable)
    spawn_swap(current, prepare(info, current, on_status, cancelled))


def cleanup_after_update() -> None:
    """Новая версия запустилась (флаг --updated): удаляем прежнюю копию."""
    if sys.platform == "darwin":
        bundle = app_bundle()
        if bundle is not None:
            shutil.rmtree(mac_backup(bundle), ignore_errors=True)
    else:
        remove_backup(Path(sys.executable))
