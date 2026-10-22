"""Детекторы речи: Silero VAD (нейросеть) и простой энергетический.

Silero отличает голос от шума вентилятора, клавиатуры и кафе. Энергетический
детектор остался для тестов и как запасной вариант.
"""

from __future__ import annotations

import threading
from typing import Protocol

import numpy as np

from govorilka import models
from govorilka.config import SAMPLE_RATE


class SpeechDetector(Protocol):
    frame_size: int  # сколько отсчётов 16 кГц в одном кадре

    def is_speech(self, frame: np.ndarray) -> bool: ...


class EnergyDetector:
    """Речь — то, что заметно громче фонового шума. Порог подстраивается сам."""

    frame_size = 480  # 30 мс

    def __init__(self, factor: float = 3.0, min_threshold: float = 0.004, adapt: float = 0.05) -> None:
        self._factor = factor
        self._min = min_threshold
        self._adapt = adapt
        self._noise = min_threshold / factor

    def is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame * frame)))
        speech = rms > max(self._min, self._noise * self._factor)
        if not speech:
            self._noise += (rms - self._noise) * self._adapt
        return speech


class SileroModel:
    """Сеть Silero VAD (2 МБ). Один экземпляр на приложение, состояние — у каждого детектора своё."""

    FILE = "silero_vad.onnx"

    def __init__(self, on_status: models.StatusCallback = lambda _: None) -> None:
        import onnxruntime as rt

        models.ensure_downloaded("vad", models.VAD_NAME, "детектор речи", on_status)
        path = models.local_dir(models.VAD_NAME) / self.FILE
        options = rt.SessionOptions()
        options.intra_op_num_threads = 1  # кадры крошечные, потоки только мешают
        options.inter_op_num_threads = 1
        self._session = rt.InferenceSession(str(path), options, providers=models.CPU)
        self._lock = threading.Lock()

    def run(self, window: np.ndarray, state: np.ndarray) -> tuple[float, np.ndarray]:
        with self._lock:
            output, new_state = self._session.run(
                ["output", "stateN"],
                {"input": window[None, :], "state": state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)},
            )
        return float(output[0, 0]), new_state

    def detector(self) -> SileroDetector:
        return SileroDetector(self)


class SileroDetector:
    """Потоковый Silero: кадр 32 мс плюс 64 отсчёта контекста, с гистерезисом порога."""

    frame_size = 512
    CONTEXT = 64

    def __init__(self, model: SileroModel, threshold: float = 0.5, neg_threshold: float = 0.35) -> None:
        self._model = model
        self._threshold = threshold
        self._neg = neg_threshold
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._speaking = False

    def is_speech(self, frame: np.ndarray) -> bool:
        window = np.concatenate([self._context, frame.astype(np.float32, copy=False)])
        prob, self._state = self._model.run(window, self._state)
        self._context = window[-self.CONTEXT :]
        # начало речи — по высокому порогу, конец — по низкому: так фраза не дрожит на границе
        self._speaking = prob >= (self._neg if self._speaking else self._threshold)
        return self._speaking
