"""Нарезка непрерывного звука на фразы по паузам.

Детектор речи энергетический: порог подстраивается под фоновый шум.
Пока человек говорит, сегментер регулярно отдаёт «черновик» фразы,
а после паузы — финальный вариант.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from govorilka.config import SAMPLE_RATE

EventKind = Literal["partial", "final", "discard"]


@dataclass(frozen=True)
class SegmentEvent:
    kind: EventKind
    audio: np.ndarray
    start: float  # время начала фразы, секунды (часы источника)


@dataclass(frozen=True)
class SegmenterParams:
    frame_ms: int = 30
    silence_end_s: float = 0.7  # пауза, после которой фраза закончилась
    max_segment_s: float = 20.0  # GigaAM лучше работает на коротких кусках
    min_speech_s: float = 0.3  # короче — это щелчок или кашель
    preroll_s: float = 0.3  # захватываем немного звука до начала речи
    partial_every_s: float = 1.0
    threshold_factor: float = 3.0  # во сколько раз речь громче шума
    min_threshold: float = 0.004  # нижняя граница порога (RMS)
    noise_adapt: float = 0.05  # скорость подстройки уровня шума


class Segmenter:
    def __init__(self, params: SegmenterParams = SegmenterParams(), sample_rate: int = SAMPLE_RATE) -> None:
        self._p = params
        self._sr = sample_rate
        self._frame = int(sample_rate * params.frame_ms / 1000)
        self._frame_s = self._frame / sample_rate
        self._leftover = np.zeros(0, dtype=np.float32)
        self._noise = params.min_threshold / params.threshold_factor
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, int(params.preroll_s / self._frame_s)))
        self._reset_segment()

    def _reset_segment(self) -> None:
        self._frames: list[np.ndarray] = []
        self._in_speech = False
        self._start = 0.0
        self._voiced = 0
        self._silence = 0
        self._since_partial = 0

    @property
    def level(self) -> float:
        """Текущий порог речи — для отладки и индикатора."""
        return max(self._p.min_threshold, self._noise * self._p.threshold_factor)

    def feed(self, samples: np.ndarray, t: float) -> list[SegmentEvent]:
        """Принимает кусок звука 16 кГц моно, t — время начала куска."""
        data = np.concatenate([self._leftover, samples.astype(np.float32, copy=False)])
        base_t = t - len(self._leftover) / self._sr
        n_frames = len(data) // self._frame
        events: list[SegmentEvent] = []
        for i in range(n_frames):
            frame = data[i * self._frame : (i + 1) * self._frame]
            events.extend(self._on_frame(frame, base_t + i * self._frame_s))
        self._leftover = data[n_frames * self._frame :]
        return events

    def flush(self) -> list[SegmentEvent]:
        """Закрывает текущую фразу (например, при остановке записи)."""
        if not self._in_speech:
            return []
        return [self._close_segment()]

    def _on_frame(self, frame: np.ndarray, frame_t: float) -> list[SegmentEvent]:
        rms = float(np.sqrt(np.mean(frame * frame)))
        is_speech = rms > self.level

        if not self._in_speech:
            if not is_speech:
                self._noise += (rms - self._noise) * self._p.noise_adapt
                self._preroll.append(frame)
                return []
            self._in_speech = True
            self._frames = list(self._preroll)
            self._start = frame_t - len(self._frames) * self._frame_s
            self._preroll.clear()

        self._frames.append(frame)
        self._since_partial += 1
        if is_speech:
            self._voiced += 1
            self._silence = 0
        else:
            self._silence += 1

        if self._silence * self._frame_s >= self._p.silence_end_s:
            return [self._close_segment()]
        if len(self._frames) * self._frame_s >= self._p.max_segment_s:
            event = self._close_segment()
            # речь продолжается — сразу открываем следующую фразу
            self._in_speech = True
            self._start = frame_t + self._frame_s
            return [event]
        if self._since_partial * self._frame_s >= self._p.partial_every_s:
            self._since_partial = 0
            return [SegmentEvent("partial", np.concatenate(self._frames), self._start)]
        return []

    def _close_segment(self) -> SegmentEvent:
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)
        kind: EventKind = "final" if self._voiced * self._frame_s >= self._p.min_speech_s else "discard"
        event = SegmentEvent(kind, audio, self._start)
        self._reset_segment()
        return event
