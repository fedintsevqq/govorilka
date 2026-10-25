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
import logging
import sys

from govorilka import objc_bridge

log = logging.getLogger(__name__)

# NSWindowCollectionBehavior
CAN_JOIN_ALL_SPACES = 1 << 0
STATIONARY = 1 << 4
IGNORES_CYCLE = 1 << 6
FULL_SCREEN_AUXILIARY = 1 << 8
BEHAVIOR = CAN_JOIN_ALL_SPACES | STATIONARY | IGNORES_CYCLE | FULL_SCREEN_AUXILIARY
NONACTIVATING_PANEL = 1 << 7  # NSWindowStyleMaskNonactivatingPanel
LEVEL_POP_UP_MENU = 101  # NSPopUpMenuWindowLevel: над полноэкранным окном


ACTIVATION_REGULAR, ACTIVATION_ACCESSORY = 0, 1  # NSApplicationActivationPolicy


def set_activation_policy(accessory: bool) -> bool:
    """macOS: пока окно спрятано в трей, Говорилка — приложение строки меню (без значка в Dock и без привязки к рабочему
    столу: меню по горячей клавише появляется над полноэкранным окном и не перебрасывает на другой стол). Когда окно
    показывают, снова обычное приложение. Любая ошибка проглатывается."""
    if sys.platform != "darwin":
        return False
    try:
        objc = objc_bridge.runtime()
        app = objc_bridge.send(objc, objc.objc_getClass(b"NSApplication"), "sharedApplication")
        policy = ACTIVATION_ACCESSORY if accessory else ACTIVATION_REGULAR
        ok = bool(objc_bridge.send(objc, app, "setActivationPolicy:", ctypes.c_bool, (ctypes.c_long,), (policy,)))
        if ok and not accessory:
            objc_bridge.send(objc, app, "activateIgnoringOtherApps:", None, (ctypes.c_bool,), (True,))
        return ok
    except Exception:  # noqa: BLE001
        log.exception("Не переключил политику активации")
        return False


def apply(view_id: int, send=None) -> bool:  # noqa: ANN001
    """Настраивает окно по его winId() (на Mac это NSView). True — настройки применены. send подменяют в тестах."""
    if send is None:
        if sys.platform != "darwin":
            return False
        objc = objc_bridge.runtime()

        def send(receiver, selector, restype=ctypes.c_void_p, argtypes=(), args=()):  # noqa: ANN001, ANN202
            return objc_bridge.send(objc, receiver, selector, restype, argtypes, args)

    try:
        window = send(view_id, "window")
        if not window:
            return False
        # панель без активации приложения: меню над полноэкранным окном не должно переключать рабочий стол
        style = send(window, "styleMask", ctypes.c_ulong)
        send(window, "setStyleMask:", None, (ctypes.c_ulong,), ((style or 0) | NONACTIVATING_PANEL,))
        send(window, "setHidesOnDeactivate:", None, (ctypes.c_bool,), (False,))
        send(window, "setCollectionBehavior:", None, (ctypes.c_ulong,), (BEHAVIOR,))
        send(window, "setLevel:", None, (ctypes.c_long,), (LEVEL_POP_UP_MENU,))
        return True
    except Exception:  # noqa: BLE001 — оформление окна не должно ронять приложение
        log.exception("Не настроил окно поверх полноэкранных приложений")
        return False
