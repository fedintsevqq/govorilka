"""Эхоподавление на синтетике: звук колонок с задержкой попадает в микрофон, поверх иногда говорю «я»."""

import queue
import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("livekit")

from govorilka.aec import FRAME, EchoCanceller, ReferenceTee  # noqa: E402
from govorilka.audio_base import AudioChunk  # noqa: E402

SR = 16_000
FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    with wave.open(str(FIXTURES / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def db(x):
    return 10 * np.log10(float(np.mean(x**2)) + 1e-12)


def run(canceller, speaker, mic, chunk=1600):
    """Как в живой записи: кусок колонок, потом кусок микрофона за то же время."""
    out, starts = [], []
    for i in range(0, len(mic), chunk):
        canceller.add_reference(speaker[i : i + chunk], i / SR)
        cleaned, start = canceller.process(mic[i : i + chunk], i / SR)
        out.append(cleaned)
        starts.append(start)
    return np.concatenate(out), starts


@pytest.fixture(scope="module")
def signals():
    speaker = np.concatenate([read("speech_ru.wav"), read("irina2.wav")])
    delay = int(0.03 * SR)  # звук из колонок доходит до микрофона через 30 мс и тише
    echo = np.concatenate([np.zeros(delay, np.float32), speaker[:-delay]]) * 0.4
    me = np.zeros_like(speaker)
    voice = read("zira1.wav") * 0.5
    at = int(9 * SR)  # говорю поверх собеседника после того, как модуль подстроился
    me[at : at + len(voice)] = voice[: len(me) - at]
    return speaker, echo, me, slice(at, at + len(voice))


def test_echo_is_suppressed(signals):
    speaker, echo, _, _ = signals
    cleaned, _ = run(EchoCanceller(), speaker, echo)
    settled = slice(4 * SR, len(cleaned))  # первые секунды модуль подстраивается
    assert db(echo[settled]) - db(cleaned[settled]) > 15


def test_my_voice_survives_while_the_other_side_talks(signals):
    speaker, echo, me, mine = signals
    cleaned, _ = run(EchoCanceller(), speaker, echo + me)
    assert db(me[mine]) - db(cleaned[mine]) < 6  # свой голос почти не теряет громкости


def test_timestamps_stay_continuous_with_uneven_chunks(signals):
    speaker, echo, _, _ = signals
    canceller = EchoCanceller()
    sizes = [1468, 1600, 1601, 1599, 1600]  # потоковый пересчёт частоты даёт куски неровной длины
    position, produced = 0, 0
    for size in sizes * 5:
        canceller.add_reference(speaker[position : position + size], position / SR)
        cleaned, start = canceller.process(echo[position : position + size], position / SR)
        assert abs(start - produced / SR) < 1e-9  # очищенный звук идёт без дыр и наложений
        produced += len(cleaned)
        position += size
    assert position - produced < FRAME  # недоделанный хвост меньше одного кадра


def test_silence_from_speakers_leaves_microphone_alone():
    rng = np.random.default_rng(1)
    me = (rng.normal(0, 0.05, SR * 3)).astype(np.float32)
    canceller = EchoCanceller()
    out = [canceller.process(me[i : i + 1600], i / SR)[0] for i in range(0, len(me), 1600)]  # колонки молчат
    assert len(np.concatenate(out)) > len(me) - FRAME


def test_reference_tee_feeds_both_and_survives_a_broken_canceller():
    target: queue.Queue = queue.Queue()

    class Broken:
        def add_reference(self, samples, t):
            raise RuntimeError("сломалось")

    tee = ReferenceTee(target, Broken())
    for _ in range(3):
        tee.put_nowait(AudioChunk(np.zeros(160, np.float32), 0.0))
    assert target.qsize() == 3  # звук колонок до распознавания доходит всегда


# ---------- включение в записи ----------


def _session(monkeypatch, tmp_path, **settings_changes):
    from dataclasses import replace

    from govorilka import session as session_module
    from govorilka.audio_base import DeviceInfo
    from govorilka.config import Settings

    class Capture:
        def __init__(self, device, out):
            self.device, self.out = device, out

        def is_alive(self):
            return True

        def stop(self):
            pass

    class Audio:
        def close(self):
            pass

        def default_mic(self):
            return DeviceInfo(1, "mic", 48_000, 1)

        def default_loopback(self):
            return DeviceInfo(2, "speakers", 48_000, 2)

        def open_capture(self, device, out):
            return Capture(device, out)

        @staticmethod
        def default_signature():
            return None

    class Detector:
        frame_size = 480

        def is_speech(self, frame):
            return False

    monkeypatch.setattr(session_module, "AudioSystem", Audio)
    monkeypatch.setattr(session_module, "WATCH_EVERY_S", 3600)
    engines = type(
        "E",
        (),
        {"recognizer": None, "vad": type("V", (), {"detector": lambda self: Detector()})(), "speakers": None},
    )()
    notices: list[str] = []
    settings = replace(Settings(transcripts_dir=str(tmp_path), split_speakers=False), **settings_changes)
    s = session_module.Session(settings, engines, "t", lambda e: None, lambda *a: None, notices.append)
    s.start()
    return s, notices


def test_echo_canceller_is_wired_between_speakers_and_microphone(monkeypatch, tmp_path):
    s, _ = _session(monkeypatch, tmp_path)
    system = next(src for src in s._sources if src.name == "system")
    assert isinstance(system.capture.out, ReferenceTee)  # звук колонок идёт ещё и образцом
    assert s._canceller is not None
    s.stop()


@pytest.mark.parametrize("changes", [{"echo_filter": False}, {"capture_mic": False}])
def test_no_echo_canceller_when_not_needed(monkeypatch, tmp_path, changes):
    s, _ = _session(monkeypatch, tmp_path, **changes)
    assert s._canceller is None
    assert all(not isinstance(src.capture.out, ReferenceTee) for src in s._sources)
    s.stop()


def test_recording_works_and_tells_when_echo_cancellation_is_unavailable(monkeypatch, tmp_path):
    from govorilka import session as session_module

    def broken():
        raise OSError("нет livekit_ffi")

    monkeypatch.setattr(session_module, "EchoCanceller", broken)
    s, notices = _session(monkeypatch, tmp_path)
    assert s._canceller is None and len(s.device_names) == 2  # запись идёт
    assert any("Эхоподавление недоступно" in n for n in notices)
    s.stop()


# ---------- по итогам ревью ----------


def test_microphone_does_not_wait_when_speakers_are_silent():
    import time

    canceller = EchoCanceller()
    now = time.monotonic()  # время как у живой записи: ожидание образца считается от него
    started = time.perf_counter()
    for i in range(5):
        canceller.process(np.zeros(1600, np.float32), now + i * 0.1)
    assert time.perf_counter() - started < 0.1  # раньше каждый кусок ждал образец по 150 мс


def test_jittery_speaker_timestamps_are_laid_end_to_end():
    canceller = EchoCanceller()
    chunk = np.ones(1600, np.float32)
    jitter = [0.0, 0.103, 0.198, 0.304, 0.399]  # время колбэка плавает на миллисекунды
    for t in jitter:
        canceller.add_reference(chunk, t)
    known = canceller._stamps >= 0
    assert known.sum() == 1600 * 5  # ни наложений, ни дыр: 5 кусков легли подряд
    assert canceller._reference_next == 1600 * 5


def test_port_file_is_removed_on_exit(tmp_path):
    from govorilka import single_instance

    lock = single_instance.acquire(lambda: None, folder=tmp_path)
    assert (tmp_path / single_instance.PORT_NAME).exists()
    lock.close()
    assert not (tmp_path / single_instance.PORT_NAME).exists()
