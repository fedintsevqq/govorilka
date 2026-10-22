"""Связка «источник звука → нарезка на фразы → распознавание → события для UI»."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from govorilka.audio_base import AudioChunk
from govorilka.config import SAMPLE_RATE
from govorilka.recognizer import Recognizer
from govorilka.segmenter import Segmenter, SegmentEvent
from govorilka.speakers import Diarizer
from govorilka.vad import SpeechDetector

log = logging.getLogger(__name__)

QUEUE_LIMIT = 600  # ~60 секунд звука по 0.1 с

Kind = Literal["partial", "final", "discard"]


@dataclass(frozen=True)
class TextEvent:
    source: str  # mic / system / file
    speaker: str
    kind: Kind
    text: str
    start: float  # секунды от начала записи
    end: float


TextCallback = Callable[[TextEvent], None]
LevelCallback = Callable[[str, float], None]


class PhraseProcessor:
    """Превращает события сегментера в текст: распознаёт и, если нужно, определяет голос."""

    def __init__(
        self,
        source: str,
        speaker: str,
        recognizer: Recognizer,
        on_text: TextCallback,
        diarizer: Diarizer | None = None,
    ) -> None:
        self.source = source
        self.speaker = speaker
        self._recognizer = recognizer
        self._on_text = on_text
        self._diarizer = diarizer

    def handle(self, event: SegmentEvent) -> None:
        end = event.start + len(event.audio) / SAMPLE_RATE
        if event.kind == "discard":
            self._emit("discard", self.speaker, "", event.start, end)
            return
        try:
            text = self._recognizer.transcribe(event.audio)
            speaker = self._speaker_for(event) if text else self.speaker
        except Exception:  # одна сломанная фраза не должна ронять запись
            log.exception("Ошибка распознавания (%s)", self.speaker)
            if event.kind == "final":
                self._emit("discard", self.speaker, "", event.start, end)
            return
        if event.kind == "final" and not text:
            self._emit("discard", self.speaker, "", event.start, end)
            return
        self._emit(event.kind, speaker, text, event.start, end)

    def _speaker_for(self, event: SegmentEvent) -> str:
        # черновики голос не определяют: дорого и бессмысленно, фраза ещё не закончена
        if self._diarizer is None or event.kind != "final":
            return self.speaker
        return f"{self.speaker} {self._diarizer.assign(event.audio)}"

    def _emit(self, kind: Kind, speaker: str, text: str, start: float, end: float) -> None:
        try:
            self._on_text(TextEvent(self.source, speaker, kind, text, start, end))
        except Exception:
            log.exception("Ошибка в обработчике текста")


class SourcePipeline:
    """Отдельный поток на каждый живой источник: микрофон и звук компьютера."""

    def __init__(
        self,
        processor: PhraseProcessor,
        detector: SpeechDetector,
        session_t0: float,
        on_level: LevelCallback = lambda *_: None,
    ) -> None:
        self.processor = processor
        self.queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=QUEUE_LIMIT)
        self._t0 = session_t0
        self._on_level = on_level
        self._segmenter = Segmenter(detector=detector)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"pipeline-{processor.source}", daemon=True)

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
                log.exception("Ошибка обработки звука (%s)", self.processor.source)
        try:
            for event in self._segmenter.flush():
                self.processor.handle(event)
        except Exception:
            log.exception("Ошибка при закрытии последней фразы (%s)", self.processor.source)

    def _process(self, chunk: AudioChunk) -> None:
        self._on_level(self.processor.source, float(np.abs(chunk.samples).max(initial=0.0)))
        # если распознавание отстаёт или запись останавливается, черновики пропускаем — финальные фразы важнее
        skip_partials = self._stop.is_set() or self.queue.qsize() > 5
        for event in self._segmenter.feed(chunk.samples, chunk.t - self._t0):
            if event.kind == "partial" and skip_partials:
                continue
            self.processor.handle(event)
