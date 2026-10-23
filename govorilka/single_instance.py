"""Один экземпляр приложения.

Если Говорилка уже запущена (например, свёрнута в фоне после автозапуска), повторный запуск
не открывает вторую копию, а просит первую показать окно. Две копии боролись бы за горячую клавишу
и за микрофон.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

PORT = 47653
SHOW = b"show\n"


ACCEPT_TIMEOUT_S = 0.5  # как часто поток приёма проверяет, не пора ли завершиться


class InstanceLock:
    def __init__(self, server: socket.socket, stop: threading.Event, thread: threading.Thread) -> None:
        self._server = server
        self._stop = stop
        self._thread = thread

    def close(self) -> None:
        """Освобождает порт. Сначала ждём, пока поток приёма выйдет: close() сокета не будит accept() на macOS и Linux."""
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._server.close()
        except OSError:
            pass


def acquire(on_show: Callable[[], None], port: int = PORT, notify_existing: bool = True) -> InstanceLock | None:
    """Возвращает замок, если мы первый экземпляр, и None, если уже есть другой (ему отправлено «покажись»)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != "win32":
        # На macOS и Linux порт после закрытия несколько секунд занят «хвостами» соединений, и без этого флага
        # перезапуск приложения не смог бы занять его снова. Две живые копии он не пропускает.
        # На Windows этот флаг, наоборот, разрешил бы вторую копию, поэтому там его нет.
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", port))  # только локально
        server.listen(4)
        server.settimeout(ACCEPT_TIMEOUT_S)
    except OSError:
        server.close()
        if notify_existing:
            _ask_to_show(port)
        return None
    stop = threading.Event()
    thread = threading.Thread(target=_serve, args=(server, on_show, stop), name="single-instance", daemon=True)
    thread.start()
    return InstanceLock(server, stop, thread)


def _ask_to_show(port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as conn:
            conn.sendall(SHOW)
    except OSError:
        log.info("Другой экземпляр занял порт, но не отвечает")


def _serve(server: socket.socket, on_show: Callable[[], None], stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn, _ = server.accept()
        except TimeoutError:
            continue
        except OSError:
            return  # сокет закрыт
        with conn:
            try:
                conn.settimeout(1.0)
                if conn.recv(16).startswith(b"show"):
                    on_show()
            except (OSError, TimeoutError):
                continue
