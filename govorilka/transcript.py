"""Транскрипт созвона: реплики, Markdown и автосохранение на диск."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SOURCE_MARK = "mark"  # отметка момента («тут обсуждали баг»)
SOURCE_NOTE = "note"  # служебная строка: пауза, продолжение записи


@dataclass(frozen=True)
class Utterance:
    speaker: str
    start: float  # секунды от начала записи
    text: str
    end: float = -1.0  # -1 — конец неизвестен, считаем равным началу
    source: str = ""  # откуда звук: mic, system или file (см. config.SOURCE_*)
    voice_id: str = ""  # стабильный ярлык голоса («Собеседник 2»); speaker — то, что показываем (может быть именем)

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
        self.aliases: dict[str, str] = {}  # ярлык голоса -> имя, которое дал пользователь или узнала память голосов
        self.centroids: dict[str, np.ndarray] = {}  # ярлык голоса -> усреднённый отпечаток (для памяти голосов)
        self.user_named: set[str] = set()  # ярлыки, которым имя дал пользователь (а не угадала память голосов)
        self.summary = ""  # итоги созвона (Markdown), если их составили
        self._lock = threading.Lock()

    def add(self, utterance: Utterance) -> None:
        """Добавляет реплику. Если говорящего уже назвали, новая реплика сразу приходит с его именем."""
        with self._lock:
            name = self.aliases.get(utterance.voice_id) if utterance.voice_id else None
            if name and utterance.speaker != name:
                utterance = replace(utterance, speaker=name)
            self._items = sorted([*self._items, utterance], key=lambda u: u.start)

    def edit_note(self, old: Utterance, text: str | None) -> bool:
        """Меняет текст отметки или убирает её (text=None). Работает только с отметками и заметками, не с репликами людей."""
        with self._lock:
            for index, item in enumerate(self._items):
                if item is old or item == old:
                    if item.source not in (SOURCE_MARK, SOURCE_NOTE):
                        return False
                    if text is None:
                        del self._items[index]
                    else:
                        self._items[index] = replace(item, text=text)
                    return True
        return False

    def relabel(self, source: str, labels: dict[float, str]) -> int:
        """Переподписывает прошлые реплики источника после пересчёта голосов (ключ — время начала реплики).

        Имя едет за человеком: если ярлык, которому дали имя, исчез (его реплики переехали к другому номеру),
        имя переходит туда, куда ушло больше всего его реплик, — если там своего имени ещё нет.
        Всё под одной блокировкой: параллельно добавленная реплика другого источника не потеряется."""
        if not labels:
            return 0
        with self._lock:
            moved: dict[str, dict[str, int]] = {}
            items, changed = [], 0
            for u in self._items:
                new = labels.get(u.start) if u.source == source else None
                if new is not None and new != u.voice_id:
                    old = u.voice_id or u.speaker
                    moved.setdefault(old, {}).setdefault(new, 0)
                    moved[old][new] += 1
                    u = replace(u, voice_id=new)
                    changed += 1
                items.append(u)
            present = {u.voice_id for u in items if u.voice_id}
            for old, targets in moved.items():
                if old in present or old not in self.aliases:
                    continue
                target = max(targets, key=targets.__getitem__)
                if target not in self.aliases:
                    self.aliases[target] = self.aliases[old]
                    if old in self.user_named:
                        self.user_named.add(target)
                del self.aliases[old]
                self.user_named.discard(old)
            self._items = [
                replace(u, speaker=self.aliases.get(u.voice_id, u.voice_id)) if u.source == source and u.voice_id else u
                for u in items
            ]
        return changed

    def name_voices(self, names: dict[str, str]) -> list[str]:
        """Подписывает голоса, у которых имени ещё нет (имена из разговора). Пользовательские имена не трогает.
        Такие имена не считаются «данными пользователем»: в память голосов они не попадут. Возвращает новые имена."""
        with self._lock:
            fresh = {v: n for v, n in names.items() if v not in self.aliases and n.strip()}
            if not fresh:
                return []
            self.aliases.update(fresh)
            self._items = [replace(u, speaker=fresh[u.voice_id]) if u.voice_id in fresh else u for u in self._items]
        return list(fresh.values())

    def display_name(self, voice_id: str) -> str:
        """Как сейчас показывать говорящего: имя, если его дали, иначе сам ярлык."""
        with self._lock:
            return self.aliases.get(voice_id, voice_id)

    def rename_voice(self, voice_id: str, name: str) -> int:
        """Переименовывает говорящего во всех его репликах. Возвращает, сколько реплик поменялось."""
        name = name.strip()
        if not name:
            return 0
        changed = 0
        with self._lock:
            if not any((u.voice_id or u.speaker) == voice_id for u in self._items):
                return 0  # такого голоса уже нет (ярлыки переставило уточнение): чужое имя не заводим
            items = []
            for u in self._items:
                if (u.voice_id or u.speaker) == voice_id and u.speaker != name:
                    u = replace(u, speaker=name, voice_id=u.voice_id or voice_id)
                    changed += 1
                items.append(u)
            self._items = items
            self.aliases[voice_id] = name
            self.user_named.add(voice_id)
        return changed

    def replace_all(self, items: list[Utterance]) -> None:
        with self._lock:
            self._items = sorted(items, key=lambda u: u.start)

    def snapshot(self) -> tuple[list[Utterance], dict[str, str], set[str]]:
        """Реплики, имена и «имена от пользователя» одним согласованным снимком."""
        with self._lock:
            return list(self._items), dict(self.aliases), set(self.user_named)

    def centroids_snapshot(self) -> dict[str, np.ndarray]:
        with self._lock:
            return dict(self.centroids)

    def apply_refined(
        self,
        items: list[Utterance],
        aliases: dict[str, str],
        centroids: dict[str, np.ndarray],
        user_named: set[str],
    ) -> None:
        """Применяет результат уточнения голосов целиком и сразу: реплики, имена и отпечатки меняются под одной блокировкой."""
        with self._lock:
            self._items = sorted(items, key=lambda u: u.start)
            self.aliases = dict(aliases)
            self.centroids = dict(centroids)
            self.user_named = set(user_named)

    def restore(self, state: tuple[list[Utterance], dict[str, str], set[str], dict[str, np.ndarray]]) -> None:
        """Возвращает транскрипт к снимку (если уточнение не удалось сохранить)."""
        items, aliases, user_named, centroids = state
        self.apply_refined(items, aliases, centroids, user_named)

    def full_state(self) -> tuple[list[Utterance], dict[str, str], set[str], dict[str, np.ndarray]]:
        with self._lock:
            return list(self._items), dict(self.aliases), set(self.user_named), dict(self.centroids)

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
        if self.summary.strip():  # итоги наверху: ради них файл чаще всего и открывают
            lines += ["## Итоги", "", self.summary.strip(), "", "## Разговор", ""]
        for u in self.utterances:
            lines.append(format_line(u, markdown=True))
            lines.append("")
        return "\n".join(lines)

    def to_plain_text(self) -> str:
        return "\n".join(format_line(u, markdown=False) for u in self.utterances)


def format_line(u: Utterance, markdown: bool) -> str:
    """Одна строка транскрипта. Отметки и паузы пишутся иначе, чем реплики: у них нет говорящего."""
    stamp = f"[{format_offset(u.start)}]"
    if u.source == SOURCE_MARK:
        return f"**{stamp} ★ {u.text}**" if markdown else f"{stamp} ★ {u.text}"
    if u.source == SOURCE_NOTE:
        return f"_{stamp} {u.text}_" if markdown else f"{stamp} {u.text}"
    return f"**{stamp} {u.speaker}:** {u.text}" if markdown else f"{stamp} {u.speaker}: {u.text}"


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
