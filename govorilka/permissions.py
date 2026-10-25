"""Разрешения macOS для Говорилки: узнать, что уже разрешено, показать системный запрос, сбросить.

Приложение подписано «пустой» подписью, которая меняется с каждой версией. macOS привязывает разрешение
к подписи, поэтому после обновления или переустановки в настройках может остаться строка от прежней копии:
переключатель включён, а новой версии доступа нет. Лечится сбросом разрешения: система спросит заново.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys

from govorilka.config import BUNDLE_ID, FROZEN

log = logging.getLogger(__name__)


def can_reset() -> bool:
    """Сбрасываем только у собранного приложения на Mac: у запуска из исходников нет своего идентификатора."""
    return sys.platform == "darwin" and FROZEN


def reset_permission(service: str, bundle_id: str = BUNDLE_ID) -> str | None:
    """`tccutil reset <служба> <приложение>`. None — получилось, иначе текст ошибки."""
    command = ["tccutil", "reset", service, bundle_id]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.exception("tccutil не отработал")
        return str(exc)
    if result.returncode != 0:
        log.error("tccutil вернул %s: %s", result.returncode, result.stderr.strip())
        return result.stderr.strip() or f"tccutil завершился с кодом {result.returncode}"
    return None


# службы для `tccutil reset`: всё, о чём Говорилка просит macOS
ALL_SERVICES = ("Microphone", "ScreenCapture", "ListenEvent", "Accessibility", "AppleEvents")

# строки «Конфиденциальность и безопасность»: id, раздел в ссылке, как это называется у человека, зачем
ACCESS_ROWS = (
    ("microphone", "Microphone", "Микрофон", "ваш голос — реплики «Я»"),
    ("screen", "ScreenCapture", "Запись экрана и системного звука", "голоса собеседников из Zoom, Teams, браузера"),
    ("input", "ListenEvent", "Мониторинг ввода", "горячая клавиша из любой программы"),
    ("accessibility", "Accessibility", "Универсальный доступ", "вставка разговора в поле ввода чата нейросети"),
)

CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
CORE_SERVICES = "/System/Library/Frameworks/CoreServices.framework/CoreServices"
CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
AV_FOUNDATION = "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"

# AVAuthorizationStatus: 0 ещё не спрашивали, 1 ограничено, 2 запрещено, 3 разрешено
AV_AUTHORIZED, AV_NOT_DETERMINED = 3, 0
AE_NOT_PERMITTED = -1743  # errAEEventNotPermitted; -1744 — «ещё не спрашивали», -600 — System Events не запущен


def decode_microphone(status: int | None) -> bool | None:
    """Статус AVCaptureDevice в «разрешено / запрещено / ещё не спрашивали»."""
    if status is None or status == AV_NOT_DETERMINED:
        return None
    return status == AV_AUTHORIZED


def decode_automation(code: int | None) -> bool | None:
    """Ответ AEDeterminePermissionToAutomateTarget: 0 разрешено, -1743 запрещено, прочее (не спрашивали, не запущен) — неизвестно."""
    if code == 0:
        return True
    if code == AE_NOT_PERMITTED:
        return False
    return None


def _call_bool(library: str, function: str) -> bool | None:
    try:
        fn = getattr(ctypes.CDLL(library), function)
    except (OSError, AttributeError):
        return None
    fn.restype = ctypes.c_bool
    return bool(fn())


def _microphone_code() -> int | None:
    """AVCaptureDevice.authorizationStatus(for: .audio): читает статус, ничего не спрашивая у человека."""
    from govorilka import objc_bridge

    try:
        ctypes.cdll.LoadLibrary(AV_FOUNDATION)
        objc = objc_bridge.runtime()
        device = objc.objc_getClass(b"AVCaptureDevice")
        string_class = objc.objc_getClass(b"NSString")
        media = objc_bridge.send(objc, string_class, "stringWithUTF8String:", ctypes.c_void_p, (ctypes.c_char_p,), (b"soun",))
        if not device or not media:
            return None
        return int(objc_bridge.send(objc, device, "authorizationStatusForMediaType:", ctypes.c_long, (ctypes.c_void_p,), (media,)))
    except Exception:  # noqa: BLE001 — нет сведений: покажем серым
        log.exception("Не прочитал статус микрофона")
        return None


class _AEDesc(ctypes.Structure):
    _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]


def _automation_code() -> int | None:
    """AEDeterminePermissionToAutomateTarget для System Events: читает статус, ничего не спрашивая у человека."""
    try:
        core = ctypes.CDLL(CORE_SERVICES)
        create, dispose, determine = core.AECreateDesc, core.AEDisposeDesc, core.AEDeterminePermissionToAutomateTarget
        create.restype, create.argtypes = ctypes.c_int16, [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_long, ctypes.POINTER(_AEDesc)]
        dispose.restype, dispose.argtypes = ctypes.c_int16, [ctypes.POINTER(_AEDesc)]
        determine.restype = ctypes.c_int32
        determine.argtypes = [ctypes.POINTER(_AEDesc), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_ubyte]
        bundle = b"com.apple.systemevents"
        target = _AEDesc()
        if create(0x62756E64, bundle, len(bundle), ctypes.byref(target)) != 0:  # 'bund': адресат по идентификатору
            return None
        try:
            return int(determine(ctypes.byref(target), 0x2A2A2A2A, 0x2A2A2A2A, 0))  # '****' — любое событие; 0 — не спрашивать
        finally:
            dispose(ctypes.byref(target))
    except Exception:  # noqa: BLE001
        log.exception("Не прочитал статус автоматизации")
        return None


def access_status() -> dict[str, bool | None]:
    """Что macOS уже разрешила Говорилке. True — разрешено, False — нет, None — ещё не спрашивали (или не узнать).
    Вне macOS все значения None."""
    if sys.platform != "darwin":
        return {key: None for key, *_ in ACCESS_ROWS}
    from govorilka.hotkeys import input_monitoring_status

    return {
        "microphone": decode_microphone(_microphone_code()),
        "screen": _call_bool(CORE_GRAPHICS, "CGPreflightScreenCaptureAccess"),
        "input": input_monitoring_status(),
        "accessibility": _call_bool(APPLICATION_SERVICES, "AXIsProcessTrusted"),
    }


def signing_identity() -> str:
    """Чем подписана запущенная программа: отпечаток постоянного сертификата, а у временной подписи — «adhoc-версия».
    Пустая строка — не из .app или не удалось узнать."""
    from govorilka import installer, version

    app = installer.app_bundle()
    if sys.platform != "darwin" or app is None:
        return ""
    try:
        leaf = installer.leaf_hash(installer.designated_requirement(app))
    except Exception:  # noqa: BLE001 — нет codesign или подпись нечитаема: ничего не сбрасываем
        log.exception("Не прочитал подпись программы")
        return ""
    return leaf or f"adhoc-{version.VERSION}"


def privacy_link(pane: str) -> str:
    """Ссылка на раздел «Конфиденциальность и безопасность» в Системных настройках."""
    return f"x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}"


def reset_all(bundle_id: str = BUNDLE_ID) -> list[str]:
    """Сбрасывает все разрешения Говорилки: система спросит о каждом заново. Возвращает ошибки (пусто — всё хорошо).
    Нужно после переустановки: macOS помнит строку «Говорилка» от прежней копии, а доступа новой копии нет."""
    problems = []
    for service in ALL_SERVICES:
        failure = reset_permission(service, bundle_id)
        if failure:
            problems.append(f"{service}: {failure}")
    return problems


# ---------- запрос разрешений кнопкой «Разрешить» ----------


def _request_microphone() -> None:
    """Помощник сам вызывает системный запрос на микрофон, если его ещё не было, и сообщает ответ человека."""
    from govorilka.config import AUDIOTAP_PATH

    proc = subprocess.Popen([str(AUDIOTAP_PATH), "--source", "mic"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if proc.stderr is not None:
            proc.stderr.readline()  # «ready» или «error:» приходит, когда человек ответил на запрос
    finally:
        proc.terminate()


def _request_screen() -> None:
    _call_bool(CORE_GRAPHICS, "CGRequestScreenCaptureAccess")


def _request_input() -> None:
    _call_bool(CORE_GRAPHICS, "CGRequestListenEventAccess")


def _request_accessibility() -> None:
    """AXIsProcessTrustedWithOptions с флагом «показать запрос»: система добавляет Говорилку в список."""
    ax, cf = ctypes.CDLL(APPLICATION_SERVICES), ctypes.CDLL(CORE_FOUNDATION)
    key = ctypes.c_void_p.in_dll(ax, "kAXTrustedCheckOptionPrompt")
    yes = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
    key_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
    value_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
    keys, values = (ctypes.c_void_p * 1)(key.value), (ctypes.c_void_p * 1)(yes.value)
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    options = cf.CFDictionaryCreate(None, keys, values, 1, key_callbacks, value_callbacks)
    ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    ax.AXIsProcessTrustedWithOptions(options)


def _request_automation() -> None:
    """Любое обращение к System Events вызывает запрос «Говорилка хочет управлять System Events»."""
    subprocess.run(["osascript", "-e", 'tell application "System Events" to get name'], capture_output=True, timeout=60, check=False)


REQUESTS = {
    "microphone": _request_microphone,
    "screen": _request_screen,
    "input": _request_input,
    "accessibility": _request_accessibility,
    "automation": _request_automation,
}


def request(key: str) -> None:
    """Показывает системный запрос на доступ, если macOS его ещё не показывала (иначе ничего не делает). Блокирует до
    ответа человека: зовите из фонового потока. Ошибки проглатываются: дальше откроем нужный раздел настроек."""
    if sys.platform != "darwin" or key not in REQUESTS:
        return
    try:
        REQUESTS[key]()
    except Exception:  # noqa: BLE001
        log.exception("Не вызвал запрос доступа (%s)", key)
