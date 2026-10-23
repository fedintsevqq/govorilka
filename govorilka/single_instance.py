"""Один экземпляр приложения.

Если Говорилка уже запущена (например, свёрнута в фоне после автозапуска), повторный запуск
не открывает вторую копию, а просит первую показать окно. Две копии боролись бы за горячую клавишу
и за микрофон.
"""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

PORT = 47653
SHOW = b"show\n"


class InstanceLock:
    def __init__(self, server: socket.socket) -> None:
        self._server = server

    def close(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass


def acquire(on_show: Callable[[], None], port: int = PORT, notify_existing: bool = True) -> InstanceLock | None:
    """Возвращает замок, если мы первый экземпляр, и None, если уже есть другой (ему отправлено «покажись»)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", port))  # только локально; SO_REUSEADDR не ставим: второй bind должен падать
        server.listen(4)
    except OSError:
        server.close()
        if notify_existing:
            _ask_to_show(port)
        return None
    threading.Thread(target=_serve, args=(server, on_show), name="single-instance", daemon=True).start()
    return InstanceLock(server)


def _ask_to_show(port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as conn:
            conn.sendall(SHOW)
    except OSError:
        log.info("Другой экземпляр занял порт, но не отвечает")


def _serve(server: socket.socket, on_show: Callable[[], None]) -> None:
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return  # сокет закрыт при выходе
        with conn:
            try:
                conn.settimeout(1.0)
                if conn.recv(16).startswith(b"show"):
                    on_show()
            except (OSError, TimeoutError):
                continue
