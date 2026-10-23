import queue
import wave
from pathlib import Path

import numpy as np
import pytest

from govorilka import session as session_module
from govorilka.audio_base import AudioError, DeviceInfo
from govorilka.config import Settings
from govorilka.filejob import FileJobError, decode_audio

FIXTURES = Path(__file__).parent / "fixtures"


def _read_wav(name: str) -> np.ndarray:
    with wave.open(str(FIXTURES / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def _encode(path: Path, samples: np.ndarray, codec: str, with_video: bool = False) -> None:
    import av

    with av.open(str(path), "w") as out:
        if with_video:
            video = out.add_stream("mpeg4", rate=5)
            video.width, video.height, video.pix_fmt = 64, 64, "yuv420p"
        audio = out.add_stream(codec, rate=44_100)
        audio.layout = "stereo"
        # перекодируем в 44.1 кГц стерео, чтобы проверить и ресемплинг, и сведение в моно
        frame = av.AudioFrame.from_ndarray(np.tile((samples * 32767).astype(np.int16), (1, 1)), format="s16", layout="mono")
        frame.sample_rate = 16_000
        resampler = av.AudioResampler(format=audio.format.name, layout="stereo", rate=44_100)
        for resampled in resampler.resample(frame) + resampler.resample(None):
            for packet in audio.encode(resampled):
                out.mux(packet)
        for packet in audio.encode(None):
            out.mux(packet)
        if with_video:
            image = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), dtype=np.uint8), format="rgb24")
            for packet in video.encode(image):
                out.mux(packet)
            for packet in video.encode(None):
                out.mux(packet)


@pytest.mark.parametrize(("name", "codec", "video"), [("call.mp3", "mp3", False), ("call.mp4", "aac", True)])
def test_decode_audio_from_mp3_and_mp4(tmp_path, name, codec, video):
    source = _read_wav("speech_ru.wav")
    path = tmp_path / name
    _encode(path, source, codec, video)
    audio = decode_audio(path)
    duration = audio.duration
    decoded = np.concatenate([c.samples for c in audio.chunks])
    audio.close()
    assert decoded.dtype == np.float32
    assert abs(len(decoded) / 16_000 - len(source) / 16_000) < 0.2  # длина сохраняется
    assert abs(duration - len(source) / 16_000) < 0.5


def test_decode_rejects_file_without_audio(tmp_path):
    bad = tmp_path / "note.mp3"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(FileJobError):
        decode_audio(bad)


# ---------- сторож устройств ----------


class FakeCapture:
    def __init__(self, device):
        self.device = device
        self.alive = True
        self.stopped = False

    def is_alive(self):
        return self.alive

    def stop(self):
        self.stopped = True


class FakeAudio:
    signature = ("out-1", "in-1")
    opened: list = []
    loopback_ok = True

    def close(self):
        pass

    def default_mic(self):
        return DeviceInfo(1, f"Mic {FakeAudio.signature[1]}", 48_000, 1)

    def default_loopback(self):
        if not FakeAudio.loopback_ok:
            raise AudioError("колонки отключены")
        return DeviceInfo(2, f"Speakers {FakeAudio.signature[0]}", 48_000, 2)

    def open_capture(self, device, out):
        capture = FakeCapture(device)
        FakeAudio.opened.append(capture)
        return capture

    @staticmethod
    def default_signature():
        return FakeAudio.signature


class FakeDetector:
    frame_size = 480

    def is_speech(self, frame):
        return False


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "AudioSystem", FakeAudio)
    monkeypatch.setattr(session_module, "WATCH_EVERY_S", 3600)  # проверяем вручную
    FakeAudio.signature = ("out-1", "in-1")
    FakeAudio.opened = []
    FakeAudio.loopback_ok = True
    engines = type("E", (), {"recognizer": None, "vad": type("V", (), {"detector": lambda self: FakeDetector()})(),
                             "speakers": type("S", (), {"embed": lambda self, a: a})()})()
    notices: list[str] = []
    s = session_module.Session(
        Settings(transcripts_dir=str(tmp_path)), engines, "t", lambda e: None, lambda *a: None, notices.append
    )
    s.start()
    yield s, notices
    s.stop()


def test_switching_headphones_reconnects_capture(session):
    s, notices = session
    old = list(FakeAudio.opened)
    FakeAudio.signature = ("out-2", "in-1")  # в системе выбрали другие наушники
    s._check_devices()
    assert all(c.stopped for c in old)
    assert any("out-2" in name for name in s.device_names)
    assert "переключился" in notices[-1]


def test_only_the_dead_source_is_reopened(session):
    s, notices = session
    mic, speakers = FakeAudio.opened
    mic.alive = False  # выдернули USB-микрофон
    s._check_devices()
    assert mic.stopped and not speakers.stopped  # колонки не трогаем
    assert len(FakeAudio.opened) == 3
    assert "пропало" in notices[-1]


def test_nothing_happens_when_devices_are_the_same(session):
    s, notices = session
    s._check_devices()
    assert len(FakeAudio.opened) == 2 and notices == []


def test_missing_device_is_retried_slowly_and_reported_once(session):
    s, notices = session
    FakeAudio.loopback_ok = False
    FakeAudio.signature = ("out-2", "in-1")  # наушники отключились, колонки недоступны
    s._check_devices()
    opened_after_switch = len(FakeAudio.opened)  # микрофон переоткрылся, колонки — нет
    for _ in range(5):
        s._check_devices()  # сразу повторять не должны: микрофон не дёргаем
    assert len(FakeAudio.opened) == opened_after_switch
    assert len([n for n in notices if "недоступно" in n]) == 1
    FakeAudio.loopback_ok = True
    s._last_retry = 0.0  # прошла пауза
    s._check_devices()
    assert "переключился" in notices[-1] and len(s.device_names) == 2
