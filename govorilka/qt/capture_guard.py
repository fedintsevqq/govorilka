"""Окно, которого не видно при демонстрации экрана и записи экрана.

Windows (10 версии 2004 и новее): SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE). Окно убирается из захвата экрана,
которым пользуются Zoom, Teams, Meet в браузере, OBS и снимки экрана; на мониторе вы его видите как обычно. На старой
Windows флаг не поддерживается — тогда WDA_MONITOR: вместо окна в трансляции чёрный прямоугольник.
Microsoft прямо пишет, что это не защита: фото экрана телефоном, а на некоторых видеокартах и отдельные способы захвата
окно всё равно увидят.

macOS: NSWindow.sharingType = NSWindowSharingNone. Работает с прежними способами захвата; начиная с macOS 15 программы,
которые снимают экран через ScreenCaptureKit (Zoom, Teams, Meet в браузере), могут окно всё же показать. Это надо
проверять на своей версии macOS и своей программе для звонков.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

WDA_NONE, WDA_MONITOR, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x1, 0x11
NS_WINDOW_SHARING_NONE, NS_WINDOW_SHARING_READ_ONLY = 0, 1


def protect(window_id: int, hidden: bool = True) -> str:
    """Прячет окно от захвата экрана (hidden=False возвращает как было). Возвращает, что получилось:
    "hidden" — окно исключено из захвата, "black" — в трансляции будет чёрный прямоугольник (старая Windows),
    "" — не получилось или система не поддерживает."""
    try:
        if sys.platform == "win32":
            return _protect_windows(window_id, hidden)
        if sys.platform == "darwin":
            return _protect_mac(window_id, hidden)
    except Exception:  # noqa: BLE001 — оформление окна не должно ронять приложение
        log.exception("Не спрятал окно от захвата экрана")
    return ""


def _protect_windows(hwnd: int, hidden: bool) -> str:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    user32.SetWindowDisplayAffinity.restype = ctypes.c_bool
    if not hidden:
        user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), WDA_NONE)
        return ""
    if user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), WDA_EXCLUDEFROMCAPTURE):
        return "hidden"
    if user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), WDA_MONITOR):  # Windows до 2004
        return "black"
    log.warning("SetWindowDisplayAffinity не сработал (код %s)", ctypes.GetLastError())  # type: ignore[attr-defined]
    return ""


def affinity(hwnd: int) -> int:
    """Текущее значение (для проверки): 0x11 — исключено из захвата."""
    value = ctypes.c_uint32(0)
    ctypes.windll.user32.GetWindowDisplayAffinity(ctypes.c_void_p(hwnd), ctypes.byref(value))  # type: ignore[attr-defined]
    return value.value


def _protect_mac(view_id: int, hidden: bool) -> str:
    from govorilka import objc_bridge

    objc = objc_bridge.runtime()
    window = objc_bridge.send(objc, view_id, "window")
    if not window:
        return ""
    sharing = NS_WINDOW_SHARING_NONE if hidden else NS_WINDOW_SHARING_READ_ONLY
    objc_bridge.send(objc, window, "setSharingType:", None, (ctypes.c_ulong,), (sharing,))
    return "hidden" if hidden else ""
