"""Окна-подсказки поверх полноэкранных приложений на macOS (круговое меню, плашка записи).

По умолчанию окно Qt с флагом Tool на Mac — панель, которая прячется, когда приложение не активно, и живёт на
рабочем столе Говорилки. Поэтому над полноэкранным Chrome меню не появлялось, а если и появлялось, система
перебрасывала на рабочий стол приложения. Чиним настройками самого окна macOS:

- не прятать окно, когда приложение неактивно;
- показывать на всех рабочих столах, в том числе над полноэкранными приложениями;
- уровень выше обычных окон.

Работает через Objective-C runtime (ctypes), без дополнительных зависимостей. Любая ошибка проглатывается:
хуже не будет, окно просто поведёт себя как раньше.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

log = logging.getLogger(__name__)

# NSWindowCollectionBehavior
CAN_JOIN_ALL_SPACES = 1 << 0
STATIONARY = 1 << 4
IGNORES_CYCLE = 1 << 6
FULL_SCREEN_AUXILIARY = 1 << 8
BEHAVIOR = CAN_JOIN_ALL_SPACES | STATIONARY | IGNORES_CYCLE | FULL_SCREEN_AUXILIARY
LEVEL_POP_UP_MENU = 101  # NSPopUpMenuWindowLevel: над полноэкранным окном


def _runtime():  # noqa: ANN202
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    return objc


def _send(objc, receiver: int, selector: str, restype=ctypes.c_void_p, argtypes=(), args=()):  # noqa: ANN001, ANN202
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", objc))
    return function(receiver, objc.sel_registerName(selector.encode()), *args)


def apply(view_id: int, send=None) -> bool:  # noqa: ANN001
    """Настраивает окно по его winId() (на Mac это NSView). True — настройки применены. send подменяют в тестах."""
    if send is None:
        if sys.platform != "darwin":
            return False
        objc = _runtime()

        def send(receiver, selector, restype=ctypes.c_void_p, argtypes=(), args=()):  # noqa: ANN001, ANN202
            return _send(objc, receiver, selector, restype, argtypes, args)

    try:
        window = send(view_id, "window")
        if not window:
            return False
        send(window, "setHidesOnDeactivate:", None, (ctypes.c_bool,), (False,))
        send(window, "setCollectionBehavior:", None, (ctypes.c_ulong,), (BEHAVIOR,))
        send(window, "setLevel:", None, (ctypes.c_long,), (LEVEL_POP_UP_MENU,))
        return True
    except Exception:  # noqa: BLE001 — оформление окна не должно ронять приложение
        log.exception("Не настроил окно поверх полноэкранных приложений")
        return False
