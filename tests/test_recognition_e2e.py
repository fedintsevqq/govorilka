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


def test_dictation_recognises_real_speech_from_the_microphone(engines):
    """Диктовка на настоящей модели: речь приходит кусками по 0.1 с, как из микрофона, отпустили — получили текст."""
    import queue
    import threading
    import time

    from govorilka.audio_base import AudioChunk, DeviceInfo
    from govorilka.dictation import Dictation

    speech = read_wav("speech_ru.wav")

    class Mic:
        def default_mic(self):
            return DeviceInfo(0, "mic", 16_000, 1)

        def open_capture(self, device, out: queue.Queue):
            stopped = threading.Event()

            def play():
                for i in range(0, len(speech), 1600):
                    if stopped.is_set():
                        return
                    out.put(AudioChunk(speech[i : i + 1600], i / 16_000))

            threading.Thread(target=play, daemon=True).start()

            class Capture:
                def stop(self):
                    stopped.set()

            return Capture()

    dictation = Dictation(engines, Mic())
    dictation.start()
    time.sleep(1.0)
    text = dictation.finish()
    assert "коллеги" in text.lower()


class _WavAudio:
    """Звуковая система с настоящей речью вместо устройств: микрофон и звук компьютера отдают свои записи кусками
    по 0,1 с в темпе, близком к живому (в 5 раз быстрее, чтобы тест не шёл минуту)."""

    def __init__(self, mic: np.ndarray, system: np.ndarray) -> None:
        self._audio = {"mic": mic, "system": system}

    @staticmethod
    def default_signature():
        return None

    def default_mic(self):
        from govorilka.audio_base import DeviceInfo

        return DeviceInfo(0, "mic", 16_000, 1)

    def default_loopback(self):
        from govorilka.audio_base import DeviceInfo

        return DeviceInfo(1, "system", 16_000, 1)

    def open_capture(self, device, out):
        import threading
        import time

        from govorilka.audio_base import AudioChunk

        samples = self._audio[device.name]
        stopped = threading.Event()

        def play():
            for i in range(0, len(samples), 1600):
                if stopped.is_set():
                    return
                out.put_nowait(AudioChunk(samples[i : i + 1600], i / 16_000))  # так кладут настоящие захваты
                time.sleep(0.02)

        threading.Thread(target=play, daemon=True).start()

        class Capture:
            def __init__(self):
                self.device = device

            def stop(self):
                stopped.set()

        return Capture()


@pytest.mark.parametrize("echo_filter", [False, True])
def test_live_recording_on_real_speech_writes_me_and_two_other_speakers(engines, tmp_path, monkeypatch, echo_filter):
    """Живая запись целиком: настоящие модели, речь в «микрофоне» и два голоса в «звуке компьютера». Текст появляется
    по ходу, ваши фразы подписаны «Я», собеседники различаются, транскрипт сохраняется в файл."""
    import time
    from dataclasses import replace

    from govorilka import session as session_module
    from govorilka.config import Settings

    gap = np.zeros(16_000, dtype=np.float32)
    mic = np.concatenate([read_wav("speech_ru.wav"), gap, np.zeros(16_000 * 12, dtype=np.float32)])
    system = np.concatenate([np.zeros(16_000 * 2, dtype=np.float32), read_wav("zira1.wav"), gap,
                             read_wav("irina2.wav"), gap])
    monkeypatch.setattr(session_module, "AudioSystem", lambda: _WavAudio(mic, system))
    monkeypatch.setattr(session_module.AudioSystem, "default_signature", staticmethod(lambda: None), raising=False)
    settings = replace(Settings(), transcripts_dir=str(tmp_path), capture_mic=True, capture_system=True,
                       split_speakers=True, echo_filter=echo_filter)
    events = []
    live = session_module.Session(settings, engines, "Проверка", events.append, lambda _s, _l: None)
    live.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not any(e.kind == "final" and e.source == "system" for e in events):
        time.sleep(0.2)
    time.sleep(4)  # дослушать вторую собеседницу
    path = live.stop()

    finals = [e for e in events if e.kind == "final"]
    mine = " ".join(e.text for e in finals if e.source == "mic").lower()
    assert "коллеги" in mine
    assert all(e.speaker == "Я" for e in finals if e.source == "mic")
    others = {u.speaker for u in live.transcript.utterances if u.speaker != "Я"}
    assert len(others) >= 2, others  # два разных голоса — два собеседника
    assert path is not None and "коллеги" in path.read_text(encoding="utf-8").lower()
