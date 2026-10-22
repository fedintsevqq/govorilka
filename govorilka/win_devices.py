"""Какие аудиоустройства сейчас выбраны в Windows по умолчанию.

PortAudio читает список устройств один раз при запуске и о смене наушников не знает.
Поэтому спрашиваем Windows напрямую через Core Audio (IMMDeviceEnumerator):
это дёшево, можно звать раз в пару секунд.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, c_uint, c_void_p
from ctypes.wintypes import DWORD

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

E_RENDER, E_CAPTURE = 0, 1  # EDataFlow: вывод звука и запись
E_CONSOLE = 0  # ERole: устройство «по умолчанию»
CLSID_MM_DEVICE_ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "Activate",
            (["in"], POINTER(GUID), "iid"),
            (["in"], DWORD, "dwClsCtx"),
            (["in"], c_void_p, "pActivationParams"),
            (["out"], POINTER(c_void_p), "ppInterface"),
        ),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD, "stgmAccess"), (["out"], POINTER(c_void_p), "pp")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_void_p), "ppstrId")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pdwState")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "EnumAudioEndpoints",
            (["in"], c_uint, "dataFlow"),
            (["in"], DWORD, "dwStateMask"),
            (["out"], POINTER(c_void_p), "ppDevices"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetDefaultAudioEndpoint",
            (["in"], c_uint, "dataFlow"),
            (["in"], c_uint, "role"),
            (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint"),
        ),
    ]


def _device_id(enumerator: IMMDeviceEnumerator, flow: int) -> str:
    try:
        device = enumerator.GetDefaultAudioEndpoint(flow, E_CONSOLE)
    except comtypes.COMError:
        return ""  # устройства такого типа нет вовсе (например, микрофон отключён)
    address = device.GetId()
    del device  # COM-объекты надо отпустить до CoUninitialize, иначе Release придёт в уже закрытый COM
    try:
        return ctypes.wstring_at(address)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(c_void_p(address))  # строку выделила Windows — освобождаем


def default_device_ids() -> tuple[str, str]:
    """(вывод, запись) — идентификаторы устройств по умолчанию. Можно звать из любого потока."""
    comtypes.CoInitialize()
    try:
        enumerator = comtypes.CoCreateInstance(
            CLSID_MM_DEVICE_ENUMERATOR, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        ids = _device_id(enumerator, E_RENDER), _device_id(enumerator, E_CAPTURE)
        del enumerator
        return ids
    finally:
        comtypes.CoUninitialize()
