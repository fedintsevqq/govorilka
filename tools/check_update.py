"""Проверка установки обновления на настоящем файле релиза (запускается в CI после сборки).

    python tools/check_update.py dist/Govorilka-…-windows-x64.exe
    python tools/check_update.py dist/Govorilka-…-macos-….dmg dist/Govorilka.app

Делает то же, что кнопка «Установить», только без скачивания и без запуска новой версии:
- сверяет размер и SHA-256 файла тем же кодом, что и установщик;
- Mac: монтирует образ, проверяет подпись программы в нём (тот же сертификат, что у собранной), копирует её;
- подменяет «установленную» копию скриптом-подменителем и проверяет, что на месте новая, а прежняя сохранена.
Падает с ненулевым кодом, если хоть что-то не так: тогда релиз не выйдет.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from govorilka import installer  # noqa: E402
from govorilka.updates import UpdateInfo  # noqa: E402


def finished_pid() -> int:
    """PID процесса, который уже завершился: скрипт-подменитель не будет ничего ждать."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    process.wait()
    return process.pid


def info_for(path: Path) -> UpdateInfo:
    return UpdateInfo("0.0.0", "", "", path.name, "https://github.com/check", path.stat().st_size, installer.sha256_of(path))


def check_windows(exe: Path) -> None:
    installer.verify(exe, info_for(exe))
    print("размер, MZ и SHA-256 сходятся")
    work = Path(tempfile.mkdtemp(prefix="govorilka-check-"))
    current, new = work / "Govorilka.exe", work / "Govorilka.new.exe"
    current.write_bytes(b"MZ old version")
    shutil.copyfile(exe, new)
    marker = work / "started.txt"
    script = work / "update.cmd"
    script.write_text(
        installer.swap_script(current, new, finished_pid(), restart=["cmd.exe", "/c", f"echo ok> {marker}"], max_wait=20),
        encoding="utf-8",
        newline="",
    )
    subprocess.run(["cmd.exe", "/c", str(script)], check=False, timeout=120, stdin=subprocess.DEVNULL)  # noqa: S603, S607
    time.sleep(1)
    assert installer.sha256_of(current) == installer.sha256_of(exe), "файл не заменён"
    assert Path(f"{current}{installer.BACKUP_SUFFIX}").read_bytes() == b"MZ old version", "прежняя версия не сохранена"
    assert marker.exists(), "новая версия не запущена"
    print("подмена exe прошла: новая версия на месте, прежняя сохранена, запуск выполнен")
    shutil.rmtree(work, ignore_errors=True)


def check_mac(dmg: Path, built_app: Path) -> None:
    installer.verify(dmg, info_for(dmg), windows_exe=False)
    print("размер и SHA-256 образа сходятся")
    work = Path(tempfile.mkdtemp(prefix="govorilka-check-"))
    apps = work / "Applications"
    apps.mkdir()
    current = apps / built_app.name
    subprocess.run(["ditto", str(built_app), str(current)], check=True)  # noqa: S603, S607 — «установленная» версия
    (current / "Contents" / "old-version-marker").write_text("old")  # отличает прежнюю копию (подпись ей уже не нужна)
    staged = current.with_name(installer.MAC_NEW_NAME.format(stem=current.stem))
    installer.stage_from_dmg(dmg, built_app, staged)  # подпись сверяем с чистой собранной программой
    wanted = installer.leaf_hash(installer.designated_requirement(built_app))
    print("подпись в образе цела", f"и сделана тем же сертификатом ({wanted[:12]}…)" if wanted else "(сертификата нет: сборка без секретов)")
    marker = work / "started.txt"
    script = work / "update.sh"
    script.write_text(installer.mac_swap_script(current, staged, finished_pid(), max_wait=20, restart=f"echo ok > '{marker}'"))
    subprocess.run(["/bin/sh", str(script)], check=True, timeout=120)  # noqa: S603
    assert not (current / "Contents" / "old-version-marker").exists(), "программа не заменена"
    assert (installer.mac_backup(current) / "Contents" / "old-version-marker").exists(), "прежняя версия не сохранена"
    assert marker.exists() and not staged.exists(), "новая версия не запущена"
    verified = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(current)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert verified.returncode == 0, f"подпись установленной программы не прошла проверку: {verified.stderr}"
    print("подмена .app прошла: новая версия на месте и подпись цела, прежняя сохранена")
    shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows CI в cp1252: кириллица иначе роняет вывод
    target = Path(sys.argv[1])
    try:
        if target.suffix == ".dmg":
            check_mac(target, Path(sys.argv[2]))
        else:
            check_windows(target)
    except (installer.InstallError, AssertionError, subprocess.CalledProcessError) as exc:
        print(f"::error::установка обновления не прошла проверку: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
