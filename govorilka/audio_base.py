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


def to_mono_16k(raw: bytes, channels: int, rate: int) -> np.ndarray:
    """int16 interleaved -> float32 моно 16 кГц."""
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return resample_mono(data, rate)
