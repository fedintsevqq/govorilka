"""Поле с транскриптом: дописывает новые реплики, а не перерисовывает всё.

Каждая реплика занимает ровно две строки в Text (текст и пустая строка),
поэтому реплика №i начинается со строки 2i+1. Если реплика пришла не по
порядку или фильтр эха удалил старую, перерисовывается только хвост
с места расхождения. Черновики фраз живут после метки «partials».
"""

from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from govorilka.config import SPEAKER_ME
from govorilka.pipeline import TextEvent
from govorilka.transcript import Utterance, format_offset

IS_MAC = sys.platform == "darwin"
FONT = "Helvetica Neue" if IS_MAC else "Segoe UI"
MONO = "Menlo" if IS_MAC else "Consolas"
FONT_SIZE = 13 if IS_MAC else 11  # на Mac точки мельче

ME_COLOR = "#1f6feb"
OTHER_COLORS = ("#1a7f37", "#8250df", "#bc4c00", "#0a7c86", "#cf222e", "#6e5600", "#bf3989", "#57606a")
PARTIALS = "partials"


@dataclass(frozen=True)
class Palette:
    background: str
    text: str
    time: str
    partial: str
    me: str
    others: tuple[str, ...]
    selection: str


LIGHT = Palette("#fbfbfb", "#22252a", "#8b8b8b", "#9a9a9a", ME_COLOR, OTHER_COLORS, "#cfe3ff")
DARK = Palette(
    "#1e1f22",
    "#e6e6e6",
    "#8b949e",
    "#8b949e",
    "#58a6ff",
    ("#3fb950", "#bc8cff", "#ffa657", "#39c5cf", "#ff7b72", "#d4a72c", "#f778ba", "#8b949e"),
    "#2f5d99",
)


def is_dark(red: int, green: int, blue: int) -> bool:
    """Тёмный ли цвет; компоненты 0..65535, как у winfo_rgb."""
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 65535 < 0.5


def detect_palette(widget: tk.Misc) -> Palette:
    """Тёмная палитра, если окно системы тёмное (на Mac и Windows 11 в тёмной теме)."""
    try:
        background = ttk.Style(widget).lookup("TFrame", "background") or widget.cget("background")
        return DARK if is_dark(*widget.winfo_rgb(background)) else LIGHT
    except tk.TclError:
        return LIGHT


def common_prefix(old: list[Utterance], new: list[Utterance]) -> int:
    """Сколько первых реплик совпадает (по объекту, а не по тексту)."""
    count = 0
    for a, b in zip(old, new):
        if a is not b:
            break
        count += 1
    return count


class TranscriptView:
    def __init__(self, parent: tk.Misc) -> None:
        frame = ttk.Frame(parent, padding=(10, 0, 10, 0))
        frame.pack(fill="both", expand=True)
        self.palette = detect_palette(parent)
        self.text = tk.Text(
            frame,
            wrap="word",
            font=(FONT, FONT_SIZE),
            padx=10,
            pady=8,
            relief="flat",
            bg=self.palette.background,
            fg=self.palette.text,  # без этого в тёмной теме macOS шрифт белый на светлом фоне
            insertbackground=self.palette.text,
            selectbackground=self.palette.selection,
            selectforeground=self.palette.text,
        )
        scroll = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("time", foreground=self.palette.time, font=(MONO, FONT_SIZE - 2))
        self.text.tag_configure("partial", foreground=self.palette.partial, font=(FONT, FONT_SIZE, "italic"))
        self._speaker_tags: dict[str, str] = {}
        self._rendered: list[Utterance] = []
        self.text.mark_set(PARTIALS, "1.0")
        self.text.mark_gravity(PARTIALS, "left")

    def color_of(self, speaker: str) -> str:
        if speaker == SPEAKER_ME:
            return self.palette.me
        others = [s for s in self._speaker_tags if s != SPEAKER_ME]
        index = others.index(speaker) if speaker in others else len(others)
        return self.palette.others[index % len(self.palette.others)]

    def _tag(self, speaker: str) -> str:
        if speaker not in self._speaker_tags:
            tag = f"speaker{len(self._speaker_tags)}"
            self.text.tag_configure(tag, foreground=self.color_of(speaker), font=(FONT, FONT_SIZE, "bold"))
            self._speaker_tags[speaker] = tag
        return self._speaker_tags[speaker]

    def clear(self) -> None:
        self.show([], [])

    def show(self, utterances: list[Utterance], partials: list[TextEvent]) -> None:
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        keep = common_prefix(self._rendered, utterances)
        if keep == len(self._rendered):
            self.text.delete(PARTIALS, "end")  # обычный случай: только дописываем
        else:
            self.text.delete(f"{2 * keep + 1}.0", "end")
        for u in utterances[keep:]:
            self._insert(u.start, u.speaker, u.text, None)
        self._rendered = list(utterances)
        self.text.mark_set(PARTIALS, "end-1c")
        for event in sorted(partials, key=lambda e: e.start):
            if event.text:
                self._insert(event.start, event.speaker, event.text + " …", "partial")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _insert(self, start: float, speaker: str, text: str, extra_tag: str | None) -> None:
        self.text.insert("end", f"[{format_offset(start)}] ", "time")
        self.text.insert("end", f"{speaker}: ", self._tag(speaker))
        # перенос внутри реплики сломал бы счёт строк (две строки на реплику)
        self.text.insert("end", " ".join(text.split()) + "\n\n", extra_tag or ())
