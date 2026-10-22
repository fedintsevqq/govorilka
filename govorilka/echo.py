"""Фильтр эха: если вы без наушников, микрофон слышит колонки.

Реплика микрофона считается эхом, если почти все её слова уже есть
в репликах собеседника, которые звучали в то же время.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from govorilka.transcript import Utterance

TIME_MARGIN_S = 1.5
CONTAINMENT_THRESHOLD = 0.7

_WORD = re.compile(r"[\wё]+", re.IGNORECASE)


def words(text: str) -> set[str]:
    return {w.lower().replace("ё", "е") for w in _WORD.findall(text)}


def _overlaps(a: Utterance, b: Utterance) -> bool:
    return a.start - TIME_MARGIN_S <= b.end and b.start <= a.end + TIME_MARGIN_S


def is_echo(mic: Utterance, others: Iterable[Utterance]) -> bool:
    mic_words = words(mic.text)
    if not mic_words:
        return False
    nearby: set[str] = set()
    for other in others:
        if _overlaps(mic, other):
            nearby |= words(other.text)
    if not nearby:
        return False
    return len(mic_words & nearby) / len(mic_words) >= CONTAINMENT_THRESHOLD
