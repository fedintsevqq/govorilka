"""Ключи API в системном хранилище паролей: «Диспетчер учётных данных» Windows и «Связка ключей» macOS.

В settings.json ключ не пишется никогда: файл настроек лежит открыто, его можно случайно отправить вместе с логом.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys

log = logging.getLogger(__name__)

SERVICE = "Govorilka"


class SecretError(RuntimeError):
    """Хранилище паролей отказало: текст показываем человеку."""


# ---------- Windows: Credential Manager (advapi32) ----------

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_uint64), ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", ctypes.c_uint32),
        ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def _target(name: str) -> str:
    return f"{SERVICE}:{name}"


def _win_set(name: str, value: str) -> None:
    blob = value.encode("utf-16-le")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _Credential(
        Type=CRED_TYPE_GENERIC, TargetName=_target(name), CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)), Persist=CRED_PERSIST_LOCAL_MACHINE,
        UserName=SERVICE,
    )
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0):  # type: ignore[attr-defined]
        raise SecretError(f"Windows не сохранил ключ (код {ctypes.GetLastError()})")  # type: ignore[attr-defined]


def _win_get(name: str) -> str:
    pointer = ctypes.POINTER(_Credential)()
    advapi = ctypes.windll.advapi32  # type: ignore[attr-defined]
    if not advapi.CredReadW(_target(name), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return ""
    try:
        cred = pointer.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        advapi.CredFree(pointer)


def _win_delete(name: str) -> None:
    ctypes.windll.advapi32.CredDeleteW(_target(name), CRED_TYPE_GENERIC, 0)  # type: ignore[attr-defined]


# ---------- macOS: Keychain через /usr/bin/security ----------


def _mac_set(name: str, value: str) -> None:
    # -U обновляет, если ключ уже был. Ключ передаётся аргументом: в истории оболочки его нет, процесс короткий
    result = subprocess.run(
        ["/usr/bin/security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        raise SecretError(f"Связка ключей не сохранила ключ: {result.stderr.strip()[:200]}")


def _mac_get(name: str) -> str:
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def _mac_delete(name: str) -> None:
    subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", SERVICE, "-a", name],
                   capture_output=True, timeout=20, check=False)


# ---------- общий вход ----------


def set_secret(name: str, value: str) -> None:
    """Сохраняет секрет; пустое значение удаляет его."""
    if not value:
        delete_secret(name)
        return
    if sys.platform == "win32":
        _win_set(name, value)
    elif sys.platform == "darwin":
        _mac_set(name, value)
    else:
        raise SecretError("Хранилище паролей поддерживается только на Windows и macOS")


def get_secret(name: str) -> str:
    """Секрет или пустая строка, если его нет или хранилище недоступно."""
    try:
        if sys.platform == "win32":
            return _win_get(name)
        if sys.platform == "darwin":
            return _mac_get(name)
    except Exception:  # noqa: BLE001 — нет ключа: просто попросим ввести
        log.exception("Не прочитал ключ из хранилища паролей")
    return ""


def delete_secret(name: str) -> None:
    try:
        if sys.platform == "win32":
            _win_delete(name)
        elif sys.platform == "darwin":
            _mac_delete(name)
    except Exception:  # noqa: BLE001
        log.exception("Не удалил ключ из хранилища паролей")
