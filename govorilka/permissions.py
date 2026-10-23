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
