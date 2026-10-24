"""Сброс разрешений macOS для Говорилки.

Приложение подписано «пустой» подписью, которая меняется с каждой версией. macOS привязывает разрешение
к подписи, поэтому после обновления в «Записи экрана» может остаться запись от прежней версии: переключатель
включён, а новой версии доступа нет. Лечится сбросом разрешения: система спросит заново.
"""

from __future__ import annotations

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

# строки «Конфиденциальность и безопасность»: id -> (название раздела в ссылке, как это называется у человека, зачем)
ACCESS_ROWS = (
    ("microphone", "Microphone", "Микрофон", "ваш голос — реплики «Я»"),
    ("screen", "ScreenCapture", "Запись экрана и системного звука", "голоса собеседников из Zoom, Teams, браузера"),
    ("input", "ListenEvent", "Мониторинг ввода", "горячая клавиша из любой программы"),
    ("accessibility", "Accessibility", "Универсальный доступ", "вставка разговора в чат нейросети"),
    ("automation", "Automation", "Автоматизация", "узнать, какой чат открыт, и вставить в него разговор"),
)


def _call_bool(library: str, function: str) -> bool | None:
    import ctypes

    try:
        fn = getattr(ctypes.CDLL(library), function)
    except (OSError, AttributeError):
        return None
    fn.restype = ctypes.c_bool
    return bool(fn())


def access_status() -> dict[str, bool | None]:
    """Что macOS уже разрешила Говорилке. None — не знаем (у микрофона узнать без запроса нельзя: система спросит сама
    при первой записи). Вне macOS все значения None."""
    if sys.platform != "darwin":
        return {key: None for key, *_ in ACCESS_ROWS}
    from govorilka.hotkeys import input_monitoring_status

    core = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    ax = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    return {
        "microphone": None,
        "screen": _call_bool(core, "CGPreflightScreenCaptureAccess"),
        "input": input_monitoring_status(),
        "accessibility": _call_bool(ax, "AXIsProcessTrusted"),
        "automation": None,  # узнать без запроса нельзя: macOS спросит при первой вставке
    }


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
