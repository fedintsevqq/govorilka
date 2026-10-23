"""Нарезка непрерывного звука на фразы по паузам.

Какие кадры — речь, решает детектор (Silero или энергетический). Пока человек
говорит, сегментер регулярно отдаёт «черновик» фразы, а после паузы — финальный
вариант. Если монолог тянется без пауз, фраза режется в самом тихом месте
последних секунд, чтобы не разрубить слово пополам.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from govorilka.config import SAMPLE_RATE
from govorilka.vad import EnergyDetector, SpeechDetector

EventKind = Literal["partial", "final", "discard"]


@dataclass(frozen=True)
class SegmentEvent:
    kind: EventKind
    audio: np.ndarray
    start: float  # время начала фразы, секунды (часы источника)


@dataclass(frozen=True)
class SegmenterParams:
    silence_end_s: float = 0.7  # пауза, после которой фраза закончилась
    max_segment_s: float = 20.0  # GigaAM лучше работает на коротких кусках
    cut_search_s: float = 2.0  # где искать тихое место для разреза длинной фразы
    min_speech_s: float = 0.3  # короче — это щелчок или кашель
    preroll_s: float = 0.3  # захватываем немного звука до начала речи
    partial_every_s: float = 1.0
    # Черновик распознаёт всю фразу целиком, поэтому на длинной фразе он дорожает. Интервал растёт вместе
    # с фразой (0.2 × длина): до 5 секунд — каждую секунду, на 20-й секунде — раз в 4 с.
    # На 20-секундном монологе это 88 секунд распознаваемого звука вместо 194.
    partial_growth: float = 0.2


@dataclass(frozen=True)
class _Frame:
    samples: np.ndarray
    rms: float
    speech: bool


class Segmenter:
    def __init__(
        self,
        params: SegmenterParams = SegmenterParams(),
        detector: SpeechDetector | None = None,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._p = params
        self._detector = detector or EnergyDetector()
        self._sr = sample_rate
        self._frame = self._detector.frame_size
        self._frame_s = self._frame / sample_rate
        self._leftover = np.zeros(0, dtype=np.float32)
        self._preroll: deque[_Frame] = deque(maxlen=max(1, int(params.preroll_s / self._frame_s)))
        self._reset_segment()

    def _reset_segment(self) -> None:
        self._frames: list[_Frame] = []
        self._in_speech = False
        self._start = 0.0
        self._since_partial = 0

    def feed(self, samples: np.ndarray, t: float) -> list[SegmentEvent]:
        """Принимает кусок звука 16 кГц моно, t — время начала куска."""
        data = np.concatenate([self._leftover, samples.astype(np.float32, copy=False)])
        base_t = t - len(self._leftover) / self._sr
        n_frames = len(data) // self._frame
        events: list[SegmentEvent] = []
        for i in range(n_frames):
            chunk = data[i * self._frame : (i + 1) * self._frame]
            frame = _Frame(chunk, float(np.sqrt(np.mean(chunk * chunk))), self._detector.is_speech(chunk))
            events.extend(self._on_frame(frame, base_t + i * self._frame_s))
        self._leftover = data[n_frames * self._frame :]
        return events

    def flush(self) -> list[SegmentEvent]:
        """Закрывает текущую фразу (например, при остановке записи)."""
        if not self._in_speech:
            return []
        event = self._event(self._frames, self._start)
        self._reset_segment()
        return [event]

    def _on_frame(self, frame: _Frame, frame_t: float) -> list[SegmentEvent]:
        if not self._in_speech:
            if not frame.speech:
                self._preroll.append(frame)
                return []
            self._in_speech = True
            self._frames = list(self._preroll)
            self._start = frame_t - len(self._frames) * self._frame_s
            self._preroll.clear()

        self._frames.append(frame)
        self._since_partial += 1

        if self._trailing_silence() * self._frame_s >= self._p.silence_end_s:
            return self.flush()
        if len(self._frames) * self._frame_s >= self._p.max_segment_s:
            return [self._cut_long_segment()]
        interval = max(self._p.partial_every_s, len(self._frames) * self._frame_s * self._p.partial_growth)
        if self._since_partial * self._frame_s >= interval:
            self._since_partial = 0
            return [SegmentEvent("partial", _join(self._frames), self._start)]
        return []

    def _trailing_silence(self) -> int:
        count = 0
        for frame in reversed(self._frames):
            if frame.speech:
                break
            count += 1
        return count

    def _cut_long_segment(self) -> SegmentEvent:
        """Режет в самом тихом кадре последних cut_search_s; хвост начинает следующую фразу."""
        window = min(len(self._frames) - 1, int(self._p.cut_search_s / self._frame_s))
        first = len(self._frames) - window
        # сначала ищем кадр без речи, среди них — самый тихий; при равенстве — самый поздний
        quietest = min(
            range(first, len(self._frames)), key=lambda i: (self._frames[i].speech, self._frames[i].rms, -i)
        )
        cut = quietest + 1
        head, tail = self._frames[:cut], self._frames[cut:]
        event = self._event(head, self._start)
        self._frames = tail
        self._start += cut * self._frame_s
        self._since_partial = 0
        return event

    def _event(self, frames: list[_Frame], start: float) -> SegmentEvent:
        voiced = sum(1 for f in frames if f.speech)
        kind: EventKind = "final" if voiced * self._frame_s >= self._p.min_speech_s else "discard"
        return SegmentEvent(kind, _join(frames), start)


def _join(frames: list[_Frame]) -> np.ndarray:
    return np.concatenate([f.samples for f in frames]) if frames else np.zeros(0, dtype=np.float32)
