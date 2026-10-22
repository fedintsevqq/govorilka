"""Распознавание речи моделью GigaAM через onnx-asr (без PyTorch, на CPU)."""

from __future__ import annotations

import logging
import threading

import numpy as np

from govorilka import models
from govorilka.config import SAMPLE_RATE

log = logging.getLogger(__name__)


class RecognizerError(RuntimeError):
    pass


class Recognizer:
    """Одна модель на всё приложение; вызовы из разных потоков идут по очереди."""

    def __init__(self, model_name: str, on_status: models.StatusCallback = lambda _: None) -> None:
        import onnx_asr  # тяжёлый импорт, грузим только когда нужен

        try:
            models.ensure_downloaded("asr", model_name, "модель распознавания", on_status)
            on_status("Загружаю модель распознавания…")
            self._model = onnx_asr.load_model(model_name, models.local_dir(model_name), providers=models.CPU)
        except Exception as exc:  # onnx-asr бросает свои исключения и ошибки сети
            raise RecognizerError(f"Не загрузил модель {model_name}: {exc}") from exc
        self._lock = threading.Lock()
        self.model_name = model_name

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < SAMPLE_RATE // 10:
            return ""
        with self._lock:
            text = self._model.recognize(audio.astype(np.float32, copy=False), sample_rate=SAMPLE_RATE)
        return str(text).strip()
