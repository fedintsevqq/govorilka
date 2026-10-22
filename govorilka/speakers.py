"""Различение собеседников по голосу.

Для каждой фразы считаем «отпечаток» голоса (wespeaker, 256 чисел) и сравниваем
с уже известными голосами. Похож — тот же человек, нет — новый собеседник.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from govorilka import models
from govorilka.config import SAMPLE_RATE

SAME_SPEAKER = 0.5  # косинусная близость: свой голос 0.6–0.9, чужой 0.3–0.45
MIN_EMBED_S = 1.0  # по более короткой фразе отпечаток ненадёжен
MAX_SPEAKERS = 12


class SpeakerModel:
    """Модель отпечатков голоса (26 МБ). Одна на приложение."""

    def __init__(self, on_status: models.StatusCallback = lambda _: None) -> None:
        from onnx_asr.loader import Manager

        models.ensure_downloaded("se", models.SPEAKER_REPO, "модель голосов", on_status)
        self._model = Manager(providers=models.CPU).create_se(
            models.SPEAKER_REPO, models.local_dir(models.SPEAKER_REPO)
        )
        self._lock = threading.Lock()

    def embed(self, audio: np.ndarray) -> np.ndarray:
        with self._lock:
            vector = np.asarray(self._model.embedding(audio, sample_rate=SAMPLE_RATE), dtype=np.float32).reshape(-1)
        return vector / (np.linalg.norm(vector) or 1.0)


@dataclass
class _Voice:
    total: np.ndarray
    count: int = 1

    @property
    def centroid(self) -> np.ndarray:
        return self.total / (np.linalg.norm(self.total) or 1.0)


@dataclass
class Diarizer:
    """Раздаёт номера голосам по мере их появления: 1, 2, 3…"""

    embed: object  # Callable[[np.ndarray], np.ndarray]; так проще подменить в тестах
    threshold: float = SAME_SPEAKER
    voices: list[_Voice] = field(default_factory=list)
    last: int = 1

    def assign(self, audio: np.ndarray) -> int:
        if len(audio) < MIN_EMBED_S * SAMPLE_RATE:
            return self.last  # короткое «да», «угу» — скорее всего, тот же человек
        vector = self.embed(audio)  # type: ignore[operator]
        if self.voices:
            scores = [float(vector @ v.centroid) for v in self.voices]
            best = int(np.argmax(scores))
            if scores[best] >= self.threshold or len(self.voices) >= MAX_SPEAKERS:
                voice = self.voices[best]
                self.voices[best] = _Voice(voice.total + vector, voice.count + 1)
                self.last = best + 1
                return self.last
        self.voices.append(_Voice(vector))
        self.last = len(self.voices)
        return self.last
