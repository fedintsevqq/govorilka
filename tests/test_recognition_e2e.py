"""Проверка всей цепочки на синтезированной русской речи (нужна скачанная модель)."""

import wave
from pathlib import Path

import numpy as np
import pytest

from govorilka.config import DEFAULT_MODEL, MODELS_DIR
from govorilka.recognizer import Recognizer
from govorilka.segmenter import Segmenter

FIXTURE = Path(__file__).parent / "fixtures" / "speech_ru.wav"

pytestmark = pytest.mark.skipif(not (MODELS_DIR / DEFAULT_MODEL).exists(), reason="модель не скачана")


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16_000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def test_speech_is_segmented_and_recognized():
    recognizer = Recognizer(DEFAULT_MODEL)
    audio = np.concatenate([read_wav(FIXTURE), np.zeros(16_000, dtype=np.float32)])
    seg = Segmenter()
    texts = []
    for i in range(0, len(audio), 1600):
        for event in seg.feed(audio[i : i + 1600], i / 16_000):
            if event.kind == "final":
                texts.append(recognizer.transcribe(event.audio))
    full = " ".join(texts).lower()
    print(texts)
    assert len(texts) >= 2
    for word in ("коллеги", "тестирование", "оплату", "телефона"):
        assert word in full
