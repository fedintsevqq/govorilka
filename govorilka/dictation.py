"""Диктовка в любое поле: пока зажата комбинация, пишется микрофон, после отпускания текст вставляется у курсора.

Фразы распознаются по ходу (нарезка по паузам, как в живой записи), поэтому после отпускания остаётся
распознать только последнюю: текст появляется почти сразу, даже если говорили минуту.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

from govorilka.audio import AudioChunk, AudioSystem, Capture
from govorilka.engines import Engines
from govorilka.segmenter import Segmenter

log = logging.getLogger(__name__)

MAX_S = 120.0  # дольше диктовка останавливается сама: зажатую по ошибке клавишу не пишем вечно
QUEUE_LIMIT = 600  # минута звука в очереди, если распознавание не успевает


class Dictation:
    """Одна диктовка: start() открывает микрофон, finish() закрывает его и возвращает распознанный текст."""

    def __init__(
        self,
        engines: Engines,
        audio: AudioSystem | None = None,
        on_level: Callable[[float], None] = lambda _: None,
        max_s: float = MAX_S,
        on_limit: Callable[[], None] = lambda: None,
    ) -> None:
        self._engines = engines
        self._audio = audio
        self._on_level = on_level
        self._max_s = max_s
        self._on_limit = on_limit
        self._queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=QUEUE_LIMIT)
        self._capture: Capture | None = None
        self._segmenter = Segmenter(detector=engines.vad.detector())
        self._recognize = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dictation-asr")  # фразы по порядку
        self._parts: list[Future[str | None]] = []
        self._heard_s = 0.0
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, name="dictation-reader", daemon=True)

    def start(self) -> None:
        """Открывает микрофон. AudioError / PermissionDeniedError — микрофона нет или нет доступа."""
        audio = self._audio or AudioSystem()
        self._capture = audio.open_capture(audio.default_mic(), self._queue)
        self._reader.start()

    def finish(self) -> str:
        """Закрывает микрофон, дораспознаёт последнюю фразу и возвращает весь текст."""
        self._close()
        try:
            for event in self._segmenter.flush():
                self._submit(event.audio)
            texts = [part.result() or "" for part in self._parts]
        finally:  # ошибка распознавания не должна оставлять поток диктовки висеть до выхода из программы
            self._recognize.shutdown(wait=False, cancel_futures=True)
        return join_phrases(texts)

    def cancel(self) -> None:
        self._close()
        self._recognize.shutdown(wait=False, cancel_futures=True)

    # ---------- внутри ----------

    def _close(self) -> None:
        if self._capture is not None:
            self._capture.stop()
        self._stop.set()
        if self._reader.is_alive():
            self._reader.join(timeout=5)
        self._drain()

    def _read(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._feed(chunk)
            if self._heard_s >= self._max_s:
                log.info("Диктовка дошла до предела %.0f с и остановлена", self._max_s)
                self._on_limit()
                return

    def _drain(self) -> None:
        """То, что микрофон успел отдать после остановки, тоже распознаём: последнее слово не теряется."""
        while True:
            try:
                self._feed(self._queue.get_nowait())
            except queue.Empty:
                return

    def _feed(self, chunk: AudioChunk) -> None:
        self._heard_s += len(chunk.samples) / 16_000
        if len(chunk.samples):
            self._on_level(float(np.sqrt(np.mean(chunk.samples * chunk.samples))))
        for event in self._segmenter.feed(chunk.samples, chunk.t):
            if event.kind == "final":
                self._submit(event.audio)

    def _submit(self, audio: np.ndarray) -> None:
        self._parts.append(self._recognize.submit(self._engines.recognizer.transcribe, audio))


def join_phrases(texts: list[str]) -> str:
    """Фразы через пробел. Модель сама ставит заглавные и точки, но если фразу оборвали без точки, а следующая
    начинается с заглавной, точку не дописываем: лучше оставить как сказано, чем угадывать."""
    return " ".join(t.strip() for t in texts if t and t.strip())
