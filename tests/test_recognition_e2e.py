"""Проверка всей цепочки на синтезированной речи (нужны скачанные модели)."""

import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from govorilka import models
from govorilka.config import DEFAULT_MODEL, Settings
from govorilka.engines import load_engines
from govorilka.filejob import FileJob
from govorilka.recognizer import Recognizer
from govorilka.segmenter import Segmenter

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not all(
        models.is_downloaded(kind, name)
        for kind, name in (("asr", DEFAULT_MODEL), ("vad", models.VAD_NAME), ("se", models.SPEAKER_REPO))
    ),
    reason="модели не скачаны",
)


def read_wav(name: str) -> np.ndarray:
    with wave.open(str(FIXTURES / name)) as w:
        assert w.getframerate() == 16_000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


@pytest.fixture(scope="module")
def engines():
    return load_engines(DEFAULT_MODEL, lambda _: None)


def test_speech_is_segmented_and_recognized(engines):
    audio = np.concatenate([read_wav("speech_ru.wav"), np.zeros(16_000, dtype=np.float32)])
    seg = Segmenter(detector=engines.vad.detector())
    texts = []
    for i in range(0, len(audio), 1600):
        for event in seg.feed(audio[i : i + 1600], i / 16_000):
            if event.kind == "final":
                texts.append(engines.recognizer.transcribe(event.audio))
    full = " ".join(texts).lower()
    assert len(texts) >= 2
    for word in ("коллеги", "тестирование", "оплату", "телефона"):
        assert word in full


def test_silero_ignores_noise():
    from govorilka.vad import SileroModel

    detector = SileroModel().detector()
    noise = np.random.default_rng(0).normal(0, 0.05, 16_000 * 3).astype(np.float32)
    frames = [noise[i : i + 512] for i in range(0, len(noise) - 512, 512)]
    assert sum(detector.is_speech(f) for f in frames) / len(frames) < 0.1


def test_file_with_two_voices_gets_two_speakers(engines, tmp_path):
    import av

    gap = np.zeros(16_000, dtype=np.float32)
    dialog = np.concatenate([read_wav("speech_ru.wav"), gap, read_wav("zira1.wav"), gap, read_wav("irina2.wav"), gap])
    path = tmp_path / "call.mp3"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mp3", rate=16_000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray((dialog * 32767).astype(np.int16)[None, :], format="s16", layout="mono")
        frame.sample_rate = 16_000
        resampler = av.AudioResampler(format=stream.format.name, layout="mono", rate=16_000)
        for f in resampler.resample(frame) + resampler.resample(None):
            for packet in stream.encode(f):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)

    done = {}
    job = FileJob(
        path,
        replace(Settings(), transcripts_dir=str(tmp_path)),
        engines,
        on_text=lambda _: None,
        on_progress=lambda _: None,
        on_done=lambda saved, error: done.update(saved=saved, error=error),
    )
    job.start()
    job.wait()
    assert done["error"] is None and done["saved"].exists()
    by_speaker = {}
    for u in job.transcript.utterances:
        by_speaker.setdefault(u.speaker, []).append(u.text)
    russian = [s for s, texts in by_speaker.items() if any("коллеги" in t.lower() or "смоук" in t.lower() for t in texts)]
    english = [s for s, texts in by_speaker.items() if s not in russian]
    assert len(by_speaker) == 2, by_speaker
    assert len(russian) == 1 and len(english) == 1  # Ирина — один человек, Зира — другой


def test_recognizer_still_works_standalone():
    assert "коллеги" in Recognizer(DEFAULT_MODEL).transcribe(read_wav("speech_ru.wav")).lower()
