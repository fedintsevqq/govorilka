"""Распознавание речи моделью GigaAM через onnx-asr (без PyTorch, на CPU)."""

from __future__ import annotations

import logging
import threading

import numpy as np

from govorilka import asr_models, models
from govorilka.config import SAMPLE_RATE

log = logging.getLogger(__name__)


class RecognizerError(RuntimeError):
    pass


class Recognizer:
    """Одна модель на всё приложение; вызовы из разных потоков идут по очереди."""

    def __init__(self, model_name: str, on_status: models.StatusCallback = lambda _: None) -> None:
        import onnx_asr  # тяжёлый импорт, грузим только когда нужен

        try:
            ref = asr_models.parse(model_name)
            models.ensure_downloaded("asr", model_name, "модель распознавания", on_status)
            on_status("Загружаю модель распознавания…")
            self._model = onnx_asr.load_model(
                ref.load_name, ref.local_dir, quantization=ref.quantization, providers=models.CPU
            )
        except Exception as exc:  # onnx-asr бросает свои исключения и ошибки сети
            raise RecognizerError(f"Не загрузил модель {asr_models.display_name(model_name)}: {exc}") from exc
        # Сессии onnxruntime можно вызывать из нескольких потоков сразу (проверено: результат тот же),
        # поэтому микрофон и собеседники распознаются параллельно. Счётчик нужен только черновикам.
        self._busy = 0
        self._busy_lock = threading.Lock()
        self.model_name = model_name

    def transcribe(self, audio: np.ndarray, skip_if_busy: bool = False) -> str | None:
        """Текст фразы. skip_if_busy — для черновиков: если модель уже занята, вернуть None и не ждать."""
        if audio.size < SAMPLE_RATE // 10:
            return ""
        with self._busy_lock:
            if skip_if_busy and self._busy:
                return None
            self._busy += 1
        try:
            text = self._model.recognize(audio.astype(np.float32, copy=False), sample_rate=SAMPLE_RATE)
        finally:
            with self._busy_lock:
                self._busy -= 1
        return str(text).strip()
