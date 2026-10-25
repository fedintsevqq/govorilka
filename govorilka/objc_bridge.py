"""Минимальный мост к Objective-C runtime через ctypes (macOS): вызвать метод у объекта без pyobjc."""

from __future__ import annotations

import ctypes
import ctypes.util


def runtime():  # noqa: ANN201
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    return objc


def send(objc, receiver: int, selector: str, restype=ctypes.c_void_p, argtypes=(), args=()):  # noqa: ANN001, ANN201
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", objc))
    return function(receiver, objc.sel_registerName(selector.encode()), *args)
