import hashlib
import http.server
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from govorilka import installer, updates

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="подмена файла через cmd — только Windows")


def info_for(data: bytes, url: str = "https://github.com/x/releases/download/v9/Govorilka-9-windows-x64.exe") -> updates.UpdateInfo:
    return updates.UpdateInfo("9.9.9", "https://example/p", "", "Govorilka-9-windows-x64.exe", url, len(data), hashlib.sha256(data).hexdigest())


EXE = b"MZ" + b"\x00" * 4000


def test_download_only_from_github_over_https():
    for bad in ("http://github.com/x", "https://evil.example/f.exe", "https://github.com.evil.example/f.exe", "file:///etc/passwd"):
        with pytest.raises(installer.InstallError):
            installer._check_url(bad)
    installer._check_url("https://github.com/fedintsevqq/govorilka/releases/download/v1/f.exe")
    installer._check_url("https://release-assets.githubusercontent.com/github-production-release-asset/1/f.exe")


def test_verify_accepts_the_published_file_and_rejects_anything_else(tmp_path):
    good = tmp_path / "new.exe"
    good.write_bytes(EXE)
    installer.verify(good, info_for(EXE))  # размер, MZ и SHA-256 совпали
    with pytest.raises(installer.InstallError, match="Контрольная сумма"):
        installer.verify(good, updates.UpdateInfo("9", "", "", "f", "u", len(EXE), "0" * 64))
    with pytest.raises(installer.InstallError, match="Размер"):
        installer.verify(good, updates.UpdateInfo("9", "", "", "f", "u", len(EXE) + 5, hashlib.sha256(EXE).hexdigest()))
    with pytest.raises(installer.InstallError, match="нет контрольной суммы"):
        installer.verify(good, updates.UpdateInfo("9", "", "", "f", "u", len(EXE), ""))
    text = tmp_path / "text.exe"
    text.write_bytes(b"not a program" * 100)
    with pytest.raises(installer.InstallError, match="не похож"):
        installer.verify(text, info_for(text.read_bytes()))


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Локальный HTTP-сервер вместо GitHub (проверка адреса отключена только в тесте)."""
    served = {"data": EXE}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(served["data"])))
            self.end_headers()
            self.wfile.write(served["data"])

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(installer, "_check_url", lambda url: None)
    yield f"http://127.0.0.1:{httpd.server_port}/f.exe", served
    httpd.shutdown()


def test_download_reports_progress_and_leaves_no_part_file(tmp_path, server):
    url, served = server
    served["data"] = EXE * 200
    target = tmp_path / "Govorilka.new.exe"
    statuses = []
    installer.download(url, target, len(served["data"]), statuses.append)
    assert target.read_bytes() == served["data"] and not (tmp_path / "Govorilka.new.exe.part").exists()
    assert statuses[0].startswith("Скачиваю обновление") and "%" in statuses[0]  # так кольцо загрузки узнаёт процент


def test_prepare_downloads_checks_and_refuses_a_tampered_file(tmp_path, server):
    url, served = server
    current = tmp_path / "Govorilka.exe"
    current.write_bytes(b"old")
    new = installer.prepare(info_for(EXE, url), current, lambda _s: None)
    assert new.read_bytes() == EXE and current.read_bytes() == b"old"  # старый файл пока не тронут
    served["data"] = b"MZ" + b"\x01" * 4000  # по сети пришло другое
    with pytest.raises(installer.InstallError, match="Контрольная сумма"):
        installer.prepare(info_for(EXE, url), current, lambda _s: None)
    assert not (tmp_path / "Govorilka.new.exe").exists()  # подменённый файл не остаётся лежать


def test_prepare_explains_a_folder_without_write_access(tmp_path, monkeypatch):
    current = tmp_path / "Govorilka.exe"

    def denied(self, *a, **k):
        raise PermissionError("отказано")

    monkeypatch.setattr(Path, "write_bytes", denied)
    with pytest.raises(installer.InstallError, match="нельзя записывать"):
        installer.prepare(info_for(EXE), current, lambda _s: None)


def run_script(tmp_path, *, lock=False, max_wait=20):
    """Настоящий запуск скрипта-подменителя на Windows: «старый» файл, «новый» файл и «запущенная программа»."""
    current, new = tmp_path / "Govorilka.exe", tmp_path / "Govorilka.new.exe"
    current.write_bytes(b"OLD")
    new.write_bytes(b"NEW")
    marker, fallback_marker = tmp_path / "started.txt", tmp_path / "fallback.txt"
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    script = tmp_path / "update.cmd"
    script.write_text(
        installer.swap_script(
            current, new, running.pid,
            restart=["cmd.exe", "/c", f"echo new> {marker}"],
            fallback=["cmd.exe", "/c", f"echo old> {fallback_marker}"],
            max_wait=max_wait,
        ),
        encoding="utf-8",
        newline="",
    )
    handle = open(current, "rb") if lock else None  # открытый файл нельзя переименовать: как у не закрывшейся программы
    try:
        subprocess.run(["cmd.exe", "/c", str(script)], timeout=60, check=False, capture_output=True)
    finally:
        if handle:
            handle.close()
        running.wait(timeout=10)
    time.sleep(0.6)  # «start» запускает команду отдельно
    return current, new, marker, fallback_marker, script


@windows_only
def test_swap_script_waits_for_the_program_replaces_the_file_and_starts_the_new_one(tmp_path):
    current, new, marker, fallback_marker, script = run_script(tmp_path)
    assert current.read_bytes() == b"NEW"  # файл заменён по тому же пути
    assert Path(f"{current}{installer.BACKUP_SUFFIX}").read_bytes() == b"OLD"  # прежняя версия осталась как .bak
    assert marker.exists() and not fallback_marker.exists()  # запущена новая версия
    assert not new.exists() and not script.exists()  # за собой скрипт убрал


@windows_only
def test_swap_script_gives_up_cleanly_when_the_file_stays_locked(tmp_path):
    current, new, marker, fallback_marker, script = run_script(tmp_path, lock=True, max_wait=3)
    assert current.read_bytes() == b"OLD"  # ничего не сломано: старая версия на месте
    assert fallback_marker.exists() and not marker.exists()  # и запущена именно она
    assert not new.exists()


def test_backup_is_removed_after_the_new_version_started(tmp_path):
    current = tmp_path / "Govorilka.exe"
    backup = Path(f"{current}{installer.BACKUP_SUFFIX}")
    backup.write_bytes(b"old")
    installer.remove_backup(current)
    assert not backup.exists()
    installer.remove_backup(current)  # повторно: ничего не случается
