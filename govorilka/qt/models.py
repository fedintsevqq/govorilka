"""Модель ленты транскрипта для QML.

Строки: реплики, отметки ★, служебные пометки (пауза) и черновики фраз в конце. Подряд идущие
реплики одного говорящего группируются: имя показывается только у первой. Цвет говорящего —
номер в палитре (QML сам берёт цвет под светлую или тёмную тему).

Обновляется только изменившийся хвост: при обычном дописывании в ListView вставляется одна
строка, а не перерисовывается весь созвон.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QObject, Qt, Signal

from govorilka.config import SOURCE_MIC, SPEAKER_ME
from govorilka.pipeline import TextEvent
from govorilka.transcript import SOURCE_MARK, SOURCE_NOTE, Utterance, format_offset

ME = -1  # номер цвета «Я»


@dataclass(frozen=True)
class Row:
    kind: str  # speech / mark / note / partial
    time: str
    speaker: str
    text: str
    color: int  # -1 — «Я», 0.. — номер собеседника в палитре
    first: bool  # первая реплика в группе одного говорящего
    key: object  # чем строка отличается от соседей: сама реплика или черновик источника
    voice_id: str = ""  # стабильный ярлык голоса: по нему переименовываем и красим, даже если показано имя


ROLES = {
    Qt.ItemDataRole.UserRole + 1: b"kind",
    Qt.ItemDataRole.UserRole + 2: b"time",
    Qt.ItemDataRole.UserRole + 3: b"speaker",
    Qt.ItemDataRole.UserRole + 4: b"text",
    Qt.ItemDataRole.UserRole + 5: b"colorIndex",
    Qt.ItemDataRole.UserRole + 6: b"first",
    Qt.ItemDataRole.UserRole + 7: b"voiceId",
}
_FIELDS = {role: name.decode() for role, name in ROLES.items()}
_ATTR = {"kind": "kind", "time": "time", "speaker": "speaker", "text": "text", "colorIndex": "color", "first": "first", "voiceId": "voice_id"}


class SpeakerColors:
    """Номер цвета по порядку появления говорящего: первый собеседник всегда один и тот же цвет."""

    def __init__(self) -> None:
        self._order: dict[str, int] = {}

    def index(self, speaker: str, source: str) -> int:
        if speaker == SPEAKER_ME or source == SOURCE_MIC:
            return ME
        if speaker not in self._order:
            self._order[speaker] = len(self._order)
        return self._order[speaker]

    def reset(self) -> None:
        self._order.clear()


def build_rows(
    utterances: list[Utterance],
    partials: list[TextEvent],
    colors: SpeakerColors,
    names: Callable[[str], str] = lambda voice: voice,
) -> list[Row]:
    """names — как показывать ярлык голоса недосказанной фразы (имя, если человека уже подписали)."""
    rows: list[Row] = []
    previous_speaker: str | None = None
    for u in utterances:
        if u.source == SOURCE_MARK:
            rows.append(Row("mark", format_offset(u.start), "", u.text, 0, True, u))
            previous_speaker = None
        elif u.source == SOURCE_NOTE:
            rows.append(Row("note", format_offset(u.start), "", u.text, 0, True, u))
            previous_speaker = None
        else:
            voice = u.voice_id or u.speaker
            first = voice != previous_speaker
            rows.append(Row("speech", format_offset(u.start), u.speaker, u.text, colors.index(voice, u.source), first, u, voice))
            previous_speaker = voice
    for event in sorted(partials, key=lambda e: e.start):
        if not event.text:
            continue
        rows.append(
            Row(
                "partial",
                format_offset(event.start),
                names(event.speaker),
                event.text,
                colors.index(event.speaker, event.source),
                True,
                ("partial", event.source, event.text),
                event.speaker,
            )
        )
    return rows


def common_prefix(old: list[Row], new: list[Row]) -> int:
    """Сколько первых строк совпадает целиком (та же реплика, та же группа, тот же цвет)."""
    count = 0
    for a, b in zip(old, new):
        if a != b:  # строки неизменяемые: равенство учитывает и реплику, и группу, и цвет
            break
        count += 1
    return count


class TranscriptModel(QAbstractListModel):
    countChanged = Signal()  # noqa: N815 — имя для QML

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[Row] = []
        self._colors = SpeakerColors()

    # ---------- для QML ----------

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {role: QByteArray(name) for role, name in ROLES.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        field = _FIELDS.get(role)
        if field is None:
            return None
        return getattr(self._rows[index.row()], _ATTR[field])

    # ---------- обновление ----------

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._colors.reset()
        self.endResetModel()
        self.countChanged.emit()

    def update(
        self, utterances: list[Utterance], partials: list[TextEvent], names: Callable[[str], str] = lambda v: v
    ) -> None:
        new = build_rows(utterances, partials, self._colors, names)
        keep = common_prefix(self._rows, new)
        if keep < len(self._rows):
            self.beginRemoveRows(QModelIndex(), keep, len(self._rows) - 1)
            self._rows = self._rows[:keep]
            self.endRemoveRows()
        if keep < len(new):
            self.beginInsertRows(QModelIndex(), keep, len(new) - 1)
            self._rows = new
            self.endInsertRows()
        else:
            self._rows = new
        self.countChanged.emit()

    @property
    def rows(self) -> list[Row]:
        return list(self._rows)

    def selection_text(self, start_row: int, start_pos: int, end_row: int, end_pos: int) -> str:
        """Текст выделения в ленте: от символа start_pos строки start_row до символа end_pos строки end_row.

        Внутри одной реплики — сам кусок текста. Через несколько строк — по строке на реплику, с временем и говорящим,
        как в «Скопировать весь разговор»: иначе в чате не понять, кто что сказал.
        """
        if not self._rows:
            return ""
        last = len(self._rows) - 1
        start_row, end_row = max(0, min(start_row, last)), max(0, min(end_row, last))
        if (end_row, end_pos) < (start_row, start_pos):
            start_row, start_pos, end_row, end_pos = end_row, end_pos, start_row, start_pos
        if start_row == end_row:
            row = self._rows[start_row]
            return row.text[max(0, start_pos):max(0, end_pos)] if row.kind in ("speech", "partial") else row_line(row)
        lines = []
        for number in range(start_row, end_row + 1):
            row = self._rows[number]
            if row.kind not in ("speech", "partial"):
                lines.append(row_line(row))
                continue
            text = row.text
            if number == start_row:
                text = text[max(0, start_pos):]
            if number == end_row:
                text = text[:max(0, end_pos)]
            if text.strip():
                lines.append(row_line(row, text.strip()))
        return "\n".join(lines)


def row_line(row: Row, text: str | None = None) -> str:
    """Строка ленты в том же виде, что и в скопированном разговоре: «[00:05] Собеседник 1: текст»."""
    body = row.text if text is None else text
    if row.kind == "mark":
        return f"[{row.time}] ★ {body}"
    if row.kind == "note":
        return f"[{row.time}] {body}"
    return f"[{row.time}] {row.speaker}: {body}"
