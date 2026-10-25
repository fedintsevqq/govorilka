"""Версия приложения. В собранном приложении её записывает сборка (govorilka.spec кладёт файл _build_version.py из тега
релиза v0.3.3 → «0.3.3»). Из исходников версии нет: это «dev», автопроверка обновлений там выключена."""

from __future__ import annotations

import re

try:
    from govorilka._build_version import VERSION  # создаётся при сборке, в репозитории его нет
except ImportError:  # запуск из исходников
    VERSION = "dev"

_NUMBERS = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse(text: str) -> tuple[int, int, int] | None:
    """«v0.3.2» и «0.3.2» → (0, 3, 2). Всё, что не похоже на номер версии, → None."""
    match = _NUMBERS.match(text.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def is_release() -> bool:
    """Собранное приложение с настоящим номером (а не запуск из исходников)."""
    return parse(VERSION) is not None


def is_newer(candidate: str, current: str = VERSION) -> bool:
    """candidate новее current? Номер текущей версии неизвестен (dev) — считаем её самой старой."""
    new = parse(candidate)
    if new is None:
        return False
    return new > (parse(current) or (0, 0, 0))
