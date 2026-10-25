import hashlib
import http.server
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath

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
    monkeypatch.setattr(installer, "_check_url", lambda url, allowed=None: None)
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
        subprocess.run(["cmd.exe", "/c", str(script)], timeout=60, check=False, capture_output=True, stdin=subprocess.DEVNULL, creationflags=installer.CREATE_NO_WINDOW)  # без консоли, как в жизни
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


# ---------- macOS ----------

import shutil as _shutil
from types import SimpleNamespace

sh_only = pytest.mark.skipif(sys.platform == "win32" or _shutil.which("sh") is None, reason="нужен sh")
HASH_A, HASH_B = "a" * 40, "b" * 40


def requirement(hash_=HASH_A):
    return f'designated => identifier "ru.govorilka" and certificate leaf = H"{hash_.upper()}"'


def fake_run(hashes, *, verify_ok=True, attach_ok=True, apps=("Govorilka.app",)):
    """Подставной codesign/hdiutil/ditto: hashes — отпечатки по порядку вызовов `codesign -d -r-`."""
    calls, queue = [], list(hashes)

    def run(command, timeout=300):
        calls.append(command[0])
        out = SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0] == "codesign" and command[1] == "--verify":
            out.returncode = 0 if verify_ok else 1
        elif command[0] == "codesign":
            out.stderr = requirement(queue.pop(0))
        elif command[0] == "hdiutil" and command[1] == "attach":
            if not attach_ok:
                return SimpleNamespace(returncode=1, stdout="", stderr="не образ")
            mount = Path(command[command.index("-mountpoint") + 1])
            for name in apps:
                (mount / name).mkdir()
        elif command[0] == "ditto":
            Path(command[2]).mkdir()
        return out

    return run, calls


def test_leaf_hash_is_read_from_the_designated_requirement():
    assert installer.leaf_hash(requirement()) == HASH_A
    assert installer.leaf_hash('designated => cdhash H"ab"') == ""  # подпись без постоянного сертификата


def test_signature_must_be_valid_and_from_the_same_certificate(tmp_path):
    run, _ = fake_run([HASH_A, HASH_A])
    installer.verify_app_signature(tmp_path / "new.app", tmp_path / "old.app", run)
    run, _ = fake_run([HASH_A, HASH_B])
    with pytest.raises(installer.InstallError, match="другим сертификатом"):
        installer.verify_app_signature(tmp_path / "new.app", tmp_path / "old.app", run)
    run, _ = fake_run([], verify_ok=False)
    with pytest.raises(installer.InstallError, match="повреждена"):
        installer.verify_app_signature(tmp_path / "new.app", tmp_path / "old.app", run)


def test_unsigned_installed_version_is_accepted_after_sha_check_only(tmp_path):
    def run(command, timeout=300):
        return SimpleNamespace(returncode=0, stdout="", stderr='designated => cdhash H"ab"')

    installer.verify_app_signature(tmp_path / "new.app", tmp_path / "old.app", run)


def test_stage_from_dmg_copies_the_app_and_always_detaches(tmp_path):
    current, staged = tmp_path / "Govorilka.app", tmp_path / ".Govorilka.new.app"
    run, calls = fake_run([HASH_A, HASH_A, HASH_A, HASH_A])
    installer.stage_from_dmg(tmp_path / "u.dmg", current, staged, run)
    assert staged.is_dir() and calls[-1] == "hdiutil"  # образ отсоединён
    run, calls = fake_run([HASH_A, HASH_B])
    with pytest.raises(installer.InstallError):
        installer.stage_from_dmg(tmp_path / "u.dmg", current, tmp_path / ".x.app", run)
    assert not (tmp_path / ".x.app").exists() and calls[-1] == "hdiutil"  # чужая подпись: ничего не осталось, образ отсоединён
    run, _ = fake_run([], apps=())
    with pytest.raises(installer.InstallError, match="нет программы"):
        installer.stage_from_dmg(tmp_path / "u.dmg", current, staged, run)
    run, calls = fake_run([], attach_ok=False)
    with pytest.raises(installer.InstallError, match="открыть образ"):
        installer.stage_from_dmg(tmp_path / "u.dmg", current, staged, run)
    assert "ditto" not in calls


def test_mac_refuses_to_replace_itself_from_a_dmg_or_quarantine():
    assert installer.app_bundle(PurePosixPath("/Applications/Govorilka.app/Contents/MacOS/Govorilka")) == PurePosixPath("/Applications/Govorilka.app")
    assert installer.app_bundle(PurePosixPath("/usr/bin/python3")) is None
    assert "образа" in installer.mac_location_problem(PurePosixPath("/Volumes/Govorilka/Govorilka.app"))
    assert "образа" in installer.mac_location_problem(PurePosixPath("/private/var/folders/x/AppTranslocation/ABC/d/Govorilka.app"))
    assert installer.mac_location_problem(None)


@sh_only
def test_mac_swap_script_replaces_the_bundle_keeps_a_backup_and_starts_it(tmp_path):
    current, staged = tmp_path / "Govorilka.app", tmp_path / ".Govorilka.new.app"
    (current / "Contents").mkdir(parents=True)
    (current / "Contents" / "v.txt").write_text("OLD")
    (staged / "Contents").mkdir(parents=True)
    (staged / "Contents" / "v.txt").write_text("NEW")
    marker = tmp_path / "started.txt"
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    script = tmp_path / "u.sh"
    script.write_text(installer.mac_swap_script(current, staged, running.pid, max_wait=20, restart=f"echo started > {marker}"))
    threading.Thread(target=running.wait, daemon=True).start()  # завершённого потомка надо подобрать: «зомби» отвечает на kill -0
    subprocess.run(["sh", str(script)], timeout=60, check=True)
    running.wait(timeout=10)
    assert (current / "Contents" / "v.txt").read_text() == "NEW" and not staged.exists()
    assert (installer.mac_backup(current) / "Contents" / "v.txt").read_text() == "OLD"
    assert marker.exists() and not script.exists()


def test_mac_swap_script_text_quotes_awkward_paths(tmp_path):
    text = installer.mac_swap_script(PurePosixPath("/Apps/My App's.app"), PurePosixPath("/Apps/.new.app"), 42)
    assert "'/Apps/My App'\"'\"'s.app'" in text and "kill -0 42" in text and "--updated" in text


def test_swap_script_calls_system_commands_by_full_path():
    """В PATH бывают find и ping из Git: с ними скрипт решил бы, что программа уже закрылась."""
    text = installer.swap_script(Path("C:/a/Govorilka.exe"), Path("C:/a/Govorilka.new.exe"), 42)
    assert r"%SystemRoot%\System32\find.exe" in text and r"%SystemRoot%\System32\tasklist.exe" in text
    assert "| find " not in text and "\ntimeout " not in text
