"""Вывести своё окно на передний план в Windows.

Windows защищает от «кражи фокуса»: SetForegroundWindow срабатывает, только если ввод сейчас у нашей программы.
После кругового меню фокус уже мог вернуться в программу, где вы были (браузер, видео), — тогда окно лишь мигает
на панели задач. Стандартный обход: на миг подключиться к очереди ввода окна, которое сейчас впереди.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

SW_RESTORE = 9


def bring_to_front(hwnd: int) -> bool:
    """True — окно впереди. Только Windows; на других системах ничего не делает."""
    if sys.platform != "win32" or not hwnd:
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return True
    foreground = user32.GetForegroundWindow()
    ours = ctypes.windll.kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True))
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)
    if not ok:
        log.info("Windows не дал вывести окно вперёд")
    return ok


ASFW_ANY = -1


def allow_foreground_handoff() -> None:
    """Разрешает программе, которую мы сейчас откроем (браузеру), выйти вперёд. Без этого Windows иногда открывает
    вкладку за нашим окном. Только Windows."""
    if sys.platform == "win32":
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
