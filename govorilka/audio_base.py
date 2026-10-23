"""Общие типы и преобразования звука для всех платформ."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from typing import Protocol

import numpy as np
from scipy.signal import resample_poly

from govorilka.config import SAMPLE_RATE

CHUNK_SECONDS = 0.1


@dataclass(frozen=True)
class AudioChunk:
    samples: np.ndarray  # float32, моно, 16 кГц
    t: float  # time.monotonic() начала куска


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    rate: int
    channels: int


class AudioError(RuntimeError):
    pass


class PermissionDeniedError(AudioError):
    """macOS не дала доступ к звуку. service — имя службы для `tccutil reset`: ScreenCapture или Microphone."""

    def __init__(self, service: str, message: str) -> None:
        super().__init__(message)
        self.service = service


class Capture(Protocol):
    device: DeviceInfo

    def is_alive(self) -> bool: ...

    def stop(self) -> None: ...


def resample_mono(data: np.ndarray, rate: int) -> np.ndarray:
    """float32 моно любой частоты -> 16 кГц."""
    if rate == SAMPLE_RATE:
        return data.astype(np.float32, copy=False)
    g = gcd(rate, SAMPLE_RATE)
    return resample_poly(data, SAMPLE_RATE // g, rate // g).astype(np.float32)


class StreamResampler:
    """Пересчёт в 16 кГц потоком (soxr): фильтр помнит хвост прошлого куска, поэтому на стыках нет искажений.

    Раньше каждый кусок по 0.1 с пересчитывался отдельно, и 10 раз в секунду на стыке появлялась
    ошибка до −36 дБ. Фильтр придерживает около 33 мс звука до следующего куска; для живой речи это незаметно.
    Один экземпляр на один поток звука.
    """

    def __init__(self, rate: int) -> None:
        import soxr

        self.rate = rate
        self._stream = None if rate == SAMPLE_RATE else soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")

    def process(self, mono: np.ndarray) -> np.ndarray:
        samples = mono.astype(np.float32, copy=False)
        if self._stream is None:
            return samples
        return self._stream.resample_chunk(samples)


def int16_to_mono(raw: bytes, channels: int) -> np.ndarray:
    """int16 interleaved -> float32 моно той же частоты."""
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data


def to_mono_16k(raw: bytes, channels: int, rate: int) -> np.ndarray:
    """int16 interleaved -> float32 моно 16 кГц."""
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return resample_mono(data, rate)
