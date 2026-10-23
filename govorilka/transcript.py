"""Транскрипт созвона: реплики, Markdown и автосохранение на диск."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Utterance:
    speaker: str
    start: float  # секунды от начала записи
    text: str
    end: float = -1.0  # -1 — конец неизвестен, считаем равным началу
    source: str = ""  # откуда звук: mic, system или file (см. config.SOURCE_*)

    def __post_init__(self) -> None:
        if self.end < self.start:
            object.__setattr__(self, "end", self.start)


def format_offset(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def safe_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().strip(".")
    return re.sub(r"\s+", "_", cleaned)[:60]


class Transcript:
    """Потокобезопасный список реплик, отсортированный по времени."""

    def __init__(self, started_at: datetime, title: str = "") -> None:
        self.started_at = started_at
        self.title = title
        self._items: list[Utterance] = []
        self._lock = threading.Lock()

    def add(self, utterance: Utterance) -> None:
        with self._lock:
            self._items = sorted([*self._items, utterance], key=lambda u: u.start)

    def remove(self, doomed: Iterable[Utterance]) -> None:
        doomed_ids = {id(u) for u in doomed}
        with self._lock:
            self._items = [u for u in self._items if id(u) not in doomed_ids]

    @property
    def utterances(self) -> list[Utterance]:
        with self._lock:
            return list(self._items)

    def default_filename(self) -> str:
        stamp = self.started_at.strftime("%Y-%m-%d_%H-%M")
        suffix = safe_filename(self.title)
        return f"{stamp}_{suffix}.md" if suffix else f"{stamp}.md"

    def to_markdown(self) -> str:
        heading = self.title.strip() or "Созвон"
        lines = [
            f"# {heading}",
            "",
            f"Дата: {self.started_at.strftime('%d.%m.%Y %H:%M')}",
            "",
        ]
        for u in self.utterances:
            lines.append(f"**[{format_offset(u.start)}] {u.speaker}:** {u.text}")
            lines.append("")
        return "\n".join(lines)

    def to_plain_text(self) -> str:
        return "\n".join(f"[{format_offset(u.start)}] {u.speaker}: {u.text}" for u in self.utterances)


def write_atomic(path: Path, text: str) -> None:
    """Пишет файл через временный, чтобы читатель (Claude Code) не увидел половину."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")  # одинаковые переводы строк на Windows и Mac
    os.replace(tmp, path)


def unique_path(path: Path) -> Path:
    """Если файл уже есть (вторая запись с той же темой в ту же минуту), добавляем _2, _3…"""
    if not path.exists():
        return path
    for number in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


class AutoSaver:
    """Переписывает файл транскрипта после каждой новой реплики.

    on_state вызывается только при смене состояния: текст ошибки, когда сохранение
    перестало получаться (диск полон, файл занят), и None, когда снова получилось.
    """

    def __init__(
        self,
        transcript: Transcript,
        directory: Path,
        on_state: Callable[[str | None], None] = lambda _: None,
    ) -> None:
        self.transcript = transcript
        self.path = unique_path(directory / transcript.default_filename())
        self._on_state = on_state
        self._failed = False
        self._lock = threading.Lock()  # сохраняют и поток распознавания, и окно

    def save(self) -> Path | None:
        try:
            with self._lock:
                write_atomic(self.path, self.transcript.to_markdown())
        except OSError as exc:
            log.error("Автосохранение в %s не удалось: %s", self.path, exc)
            if not self._failed:
                self._failed = True
                self._on_state(f"{exc.strerror or exc}")
            return None
        if self._failed:
            self._failed = False
            self._on_state(None)
        return self.path
