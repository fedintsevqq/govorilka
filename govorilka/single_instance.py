"""Один экземпляр приложения.

Если Говорилка уже запущена (например, свёрнута в фоне после автозапуска), повторный запуск
не открывает вторую копию, а просит первую показать окно. Две копии боролись бы за горячую клавишу
и за микрофон.

Кто первый, решает замок на файле в папке данных: система снимает его сама, даже если программа упала.
Порт для просьбы «покажись» выбирается свободный и записывается рядом. Раньше порт был фиксированный,
и если его занимала посторонняя программа, Говорилка считала себя уже запущенной и молча закрывалась.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from govorilka.config import DATA_DIR

log = logging.getLogger(__name__)

SHOW = b"show\n"
ACCEPT_TIMEOUT_S = 0.5  # как часто поток приёма проверяет, не пора ли завершиться
LOCK_NAME = "govorilka.lock"
PORT_NAME = "govorilka.port"
LOCK_RETRY_S = 0.3


def _try_lock(handle) -> bool:  # noqa: ANN001 — открытый файл
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _lock_with_retry(handle) -> bool:  # noqa: ANN001
    """Если прежняя копия только что упала, система снимает её замок за несколько миллисекунд: чуть подождём."""
    deadline = time.monotonic() + LOCK_RETRY_S
    while True:
        if _try_lock(handle):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


class InstanceLock:
    def __init__(  # noqa: PLR0913
        self, handle, server: socket.socket, stop: threading.Event, thread: threading.Thread, port_file: Path  # noqa: ANN001
    ) -> None:
        self._handle = handle
        self._port_file = port_file
        self._server = server
        self._stop = stop
        self._thread = thread

    def close(self) -> None:
        """Освобождает замок и порт. Поток приёма ждём: close() сокета не будит accept() на macOS и Linux."""
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._port_file.unlink(missing_ok=True)
        except OSError:
            pass
        for resource in (self._server, self._handle):
            try:
                resource.close()  # закрытие файла снимает и замок
            except OSError:
                pass


def acquire(
    on_show: Callable[[], None], folder: Path | None = None, notify_existing: bool = True
) -> InstanceLock | None:
    """Замок, если мы первый экземпляр. None — уже запущена другая копия (ей отправлено «покажись»)."""
    folder = folder or DATA_DIR
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / LOCK_NAME, "a+b")  # noqa: SIM115 — держим открытым, пока живёт программа
    if not _lock_with_retry(handle):
        handle.close()
        if notify_existing:
            _ask_to_show(folder)
        return None
    port_file = folder / PORT_NAME
    port_file.unlink(missing_ok=True)  # файл от упавшей копии мог указывать на порт, который теперь чужой
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))  # любой свободный порт, только локально
        server.listen(4)
        server.settimeout(ACCEPT_TIMEOUT_S)
        port_file.write_text(str(server.getsockname()[1]), encoding="ascii")
    except OSError:
        # без канала «покажись» всё равно работаем: замок уже наш, вторую копию он не пустит
        log.exception("Не получилось открыть канал для второй копии")
    stop = threading.Event()
    thread = threading.Thread(target=_serve, args=(server, on_show, stop), name="single-instance", daemon=True)
    thread.start()
    return InstanceLock(handle, server, stop, thread, port_file)


def _ask_to_show(folder: Path) -> None:
    try:
        port = int((folder / PORT_NAME).read_text(encoding="ascii").strip())
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as conn:
            conn.sendall(SHOW)
    except (OSError, ValueError):
        log.info("Другая копия запущена, но не ответила на просьбу показать окно")


def _serve(server: socket.socket, on_show: Callable[[], None], stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn, _ = server.accept()
        except TimeoutError:
            continue
        except OSError:
            return  # сокет закрыт или так и не открылся
        with conn:
            try:
                conn.settimeout(1.0)
                if conn.recv(16).startswith(b"show"):
                    on_show()
            except (OSError, TimeoutError):
                continue


# Версии до 0.2.2 искали вторую копию по этому порту. Если он занят, скорее всего, ещё работает старая
# Говорилка: она не видит замок новой, и горячая клавиша сработает в обеих. Проверка ничего не меняет,
# только подсказывает закрыть старую версию.
LEGACY_PORT = 47653


def legacy_instance_suspected(port: int = LEGACY_PORT) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        probe.close()
