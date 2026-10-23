"""Эхоподавление: убирает из микрофона звук собеседника, который играет из колонок.

Работает модуль WebRTC (из пакета livekit): звук колонок служит образцом, и модуль вычитает его
из микрофона, подстраиваясь под задержку и акустику комнаты. На записи с колонок эхо падало
на 16–26 дБ, а собеседник в микрофоне переставал распознаваться совсем; свой голос при этом
теряет около 2 дБ и распознаётся как раньше.

Звук колонок и микрофона приходит разными потоками, поэтому образец хранится по абсолютному
времени (номеру отсчёта), а микрофон при обработке берёт ровно тот кусок образца, что звучал
одновременно с ним. Когда из колонок ничего не играет, образец — тишина.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from govorilka.config import SAMPLE_RATE

log = logging.getLogger(__name__)

FRAME = SAMPLE_RATE // 100  # модуль WebRTC работает кадрами по 10 мс
REFERENCE_SECONDS = 10  # сколько звука колонок помним: микрофон может обрабатываться с отставанием
WAIT_FOR_REFERENCE_S = 0.15  # кусок колонок за то же время мог ещё не прийти — недолго ждём
# Когда из колонок тишина, захват колонок вообще не присылает кусков (так работает WASAPI loopback).
# Ждать образец в это время бессмысленно: микрофон получал бы лишние 150 мс задержки.
REFERENCE_IDLE_S = 0.3
RESYNC_SAMPLES = SAMPLE_RATE // 20  # разрыв больше 50 мс — начинаем отсчёт заново


def _to_int16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class EchoCanceller:
    """Один на запись. add_reference зовёт поток захвата колонок, process — поток микрофона."""

    def __init__(self) -> None:
        from livekit import rtc  # нативная библиотека; если её нет, сессия работает без эхоподавления

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=True, high_pass_filter=True)
        self._apm.set_stream_delay_ms(0)  # задержку модуль оценивает сам; подсказка 0 на тестах давала лучший результат
        size = REFERENCE_SECONDS * SAMPLE_RATE
        self._reference = np.zeros(size, dtype=np.float32)
        self._stamps = np.full(size, -1, dtype=np.int64)  # какой абсолютный отсчёт лежит в ячейке
        self._reference_end = -1
        self._reference_next: int | None = None  # где по счёту должен начаться следующий кусок колонок
        self._last_reference = 0.0  # time.monotonic() последнего куска колонок
        self._lock = threading.Lock()
        self._pending = np.zeros(0, dtype=np.float32)
        self._pending_start = 0  # абсолютный номер первого отсчёта в _pending

    # ---------- звук колонок ----------

    def add_reference(self, samples: np.ndarray, t: float) -> None:
        with self._lock:
            start = round(t * SAMPLE_RATE)
            # время куска считается от момента колбэка и дрожит на миллисекунды; если кусок продолжает прошлый,
            # кладём его встык, иначе в образце появились бы наложения и дыры, и фильтр сбивался бы с задержки
            if self._reference_next is not None and abs(start - self._reference_next) < RESYNC_SAMPLES:
                start = self._reference_next
            index = start + np.arange(len(samples))
            slots = index % len(self._reference)
            self._reference[slots] = samples
            self._stamps[slots] = index
            self._reference_next = start + len(samples)
            self._reference_end = max(self._reference_end, self._reference_next)
            self._last_reference = time.monotonic()

    def _reference_frame(self, start: int) -> np.ndarray:
        index = start + np.arange(FRAME)
        slots = index % len(self._reference)
        with self._lock:
            values = self._reference[slots].copy()
            known = self._stamps[slots] == index
        return np.where(known, values, 0.0).astype(np.float32)  # не было звука из колонок — образец тишина

    def _wait_for_reference(self, end: int) -> None:
        deadline = end / SAMPLE_RATE + WAIT_FOR_REFERENCE_S
        while time.monotonic() < deadline:
            with self._lock:
                if self._reference_end >= end:
                    return
                if time.monotonic() - self._last_reference > REFERENCE_IDLE_S:
                    return  # колонки молчат: образец — тишина, ждать нечего
            time.sleep(0.01)

    # ---------- микрофон ----------

    def process(self, samples: np.ndarray, t: float) -> tuple[np.ndarray, float]:
        """Очищенный микрофон и время его начала. Хвост короче 10 мс ждёт следующего куска."""
        start = round(t * SAMPLE_RATE)
        expected = self._pending_start + len(self._pending)
        if not len(self._pending) or abs(start - expected) > RESYNC_SAMPLES:
            self._pending, self._pending_start = np.zeros(0, dtype=np.float32), start
        self._pending = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        usable = len(self._pending) // FRAME * FRAME
        if not usable:
            return np.zeros(0, dtype=np.float32), self._pending_start / SAMPLE_RATE
        self._wait_for_reference(self._pending_start + usable)
        cleaned = np.empty(usable, dtype=np.float32)
        rtc = self._rtc
        for offset in range(0, usable, FRAME):
            reference = rtc.AudioFrame(_to_int16(self._reference_frame(self._pending_start + offset)), SAMPLE_RATE, 1, FRAME)
            self._apm.process_reverse_stream(reference)
            capture = rtc.AudioFrame(_to_int16(self._pending[offset : offset + FRAME]), SAMPLE_RATE, 1, FRAME)
            self._apm.process_stream(capture)
            cleaned[offset : offset + FRAME] = np.frombuffer(bytes(capture.data), dtype=np.int16) / 32768.0
        t_out = self._pending_start / SAMPLE_RATE
        self._pending = self._pending[usable:]
        self._pending_start += usable
        return cleaned, t_out


class ReferenceTee:
    """Подменяет очередь захвата колонок: каждый кусок идёт и в распознавание, и образцом в эхоподавление."""

    def __init__(self, target, canceller: EchoCanceller) -> None:  # noqa: ANN001 — queue.Queue
        self._target = target
        self._canceller = canceller
        self._failed = False

    def put_nowait(self, chunk) -> None:  # noqa: ANN001 — AudioChunk
        if not self._failed:
            try:
                self._canceller.add_reference(chunk.samples, chunk.t)
            except Exception:  # без образца эхоподавление просто перестанет помогать, звук не теряем
                self._failed = True
                log.exception("Эхоподавление: не получилось принять звук колонок")
        self._target.put_nowait(chunk)
