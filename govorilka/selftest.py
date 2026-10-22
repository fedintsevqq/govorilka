"""Проверка собранного приложения без окна: `Govorilka --selftest файл.wav`.

Грузит модель, распознаёт wav (16 кГц моно) и пишет результат в selftest.txt
в папке данных. Код выхода 0 — всё работает.
"""

from __future__ import annotations

import logging
import traceback
import wave
from pathlib import Path

import numpy as np

from govorilka.config import DATA_DIR, DEFAULT_MODEL

log = logging.getLogger(__name__)


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getframerate() != 16_000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("нужен wav 16 кГц, моно, 16 бит")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def _check_devices() -> str:
    from govorilka.audio import AudioError, AudioSystem

    audio = AudioSystem()
    try:
        lines = []
        for label, find in (("микрофон", audio.default_mic), ("системный звук", audio.default_loopback)):
            try:
                lines.append(f"{label}: {find().name}")
            except AudioError as exc:  # на сервере сборки устройств может не быть — это не провал
                lines.append(f"{label}: нет ({exc})")
        return "\n".join(lines)
    finally:
        audio.close()


def run(wav: Path) -> int:
    from govorilka.recognizer import Recognizer

    report = DATA_DIR / "selftest.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        text = Recognizer(DEFAULT_MODEL).transcribe(_read_wav(wav))
        devices = _check_devices()
        report.write_text(f"OK\n{text}\n{devices}\n", encoding="utf-8")
        return 0 if text else 1
    except Exception:
        log.exception("Самопроверка не прошла")
        report.write_text("FAIL\n" + traceback.format_exc(), encoding="utf-8")
        return 1
