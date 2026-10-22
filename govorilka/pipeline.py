"""Связка «источник звука → нарезка на фразы → распознавание → события для UI»."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from govorilka.audio import AudioChunk
from govorilka.config import SAMPLE_RATE
from govorilka.recognizer import Recognizer
from govorilka.segmenter import Segmenter

log = logging.getLogger(__name__)

QUEUE_LIMIT = 600  # ~60 секунд звука по 0.1 с


@dataclass(frozen=True)
class TextEvent:
    speaker: str
    kind: Literal["partial", "final", "discard"]
    text: str
    start: float  # секунды от начала записи
    end: float


TextCallback = Callable[[TextEvent], None]
LevelCallback = Callable[[str, float], None]


class SourcePipeline:
    """Отдельный поток на каждый источник: микрофон и звук компьютера."""

    def __init__(
        self,
        speaker: str,
        recognizer: Recognizer,
        session_t0: float,
        on_text: TextCallback,
        on_level: LevelCallback = lambda *_: None,
    ) -> None:
        self.speaker = speaker
        self.queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=QUEUE_LIMIT)
        self._recognizer = recognizer
        self._t0 = session_t0
        self._on_text = on_text
        self._on_level = on_level
        self._segmenter = Segmenter()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"pipeline-{speaker}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Дорабатывает то, что уже в очереди, и закрывает последнюю фразу."""
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not (self._stop.is_set() and self.queue.empty()):
            try:
                chunk = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process(chunk)
            except Exception:  # поток не должен умирать молча — иначе распознавание встанет
                log.exception("Ошибка обработки звука (%s)", self.speaker)
        try:
            for event in self._segmenter.flush():
                self._handle(event.kind, event.audio, event.start)
        except Exception:
            log.exception("Ошибка при закрытии последней фразы (%s)", self.speaker)

    def _process(self, chunk: AudioChunk) -> None:
        self._on_level(self.speaker, float(np.abs(chunk.samples).max(initial=0.0)))
        # если распознавание отстаёт или запись останавливается, черновики пропускаем — финальные фразы важнее
        skip_partials = self._stop.is_set() or self.queue.qsize() > 5
        for event in self._segmenter.feed(chunk.samples, chunk.t - self._t0):
            if event.kind == "partial" and skip_partials:
                continue
            self._handle(event.kind, event.audio, event.start)

    def _handle(self, kind: str, audio: np.ndarray, start: float) -> None:
        end = start + len(audio) / SAMPLE_RATE
        if kind == "discard":
            self._emit("discard", "", start, end)
            return
        try:
            text = self._recognizer.transcribe(audio)
        except Exception:  # одна сломанная фраза не должна ронять запись
            log.exception("Ошибка распознавания (%s)", self.speaker)
            if kind == "final":
                self._emit("discard", "", start, end)
            return
        if kind == "final" and not text:
            self._emit("discard", "", start, end)
            return
        self._emit(kind, text, start, end)  # type: ignore[arg-type]

    def _emit(self, kind: Literal["partial", "final", "discard"], text: str, start: float, end: float) -> None:
        try:
            self._on_text(TextEvent(self.speaker, kind, text, start, end))
        except Exception:
            log.exception("Ошибка в обработчике текста")
