"""Помощник macOS и разрешения. Настоящий помощник на Windows не запустить, поэтому подставляем свой процесс."""

import io
import queue
import subprocess

import numpy as np
import pytest

from govorilka import audio_mac, permissions
from govorilka.audio_base import AudioError, PermissionDeniedError


class FakeProcess:
    """Ведёт себя как subprocess.Popen: строки stderr, байты stdout, poll()."""

    def __init__(self, stderr_lines, audio=b"", exit_code=None):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(audio)
        self.stderr = io.BytesIO(("\n".join(stderr_lines) + "\n").encode("utf-8") if stderr_lines else b"")
        self._exit_code = exit_code
        self.terminated = False

    def poll(self):
        return self._exit_code

    def terminate(self):
        self.terminated = True
        self._exit_code = -15

    def wait(self, timeout=None):
        return self._exit_code

    def kill(self):
        self._exit_code = -9


@pytest.fixture
def fake_helper(monkeypatch):
    def install(process):
        monkeypatch.setattr(audio_mac.subprocess, "Popen", lambda *a, **k: process)
        return process

    monkeypatch.setattr(audio_mac, "TAP_READY_TIMEOUT_S", 3)
    return install


def open_capture(source="system"):
    device = audio_mac.SYSTEM_DEVICE if source == "system" else audio_mac.MIC_DEVICE
    return audio_mac.AudioSystem().open_capture(device, queue.Queue())


# ---------- «нет доступа» и «другая причина» не путаются ----------


@pytest.mark.parametrize(
    ("line", "service"),
    [
        ("error: permission: screen: The user declined TCCs (code -3801)", "ScreenCapture"),
        ("error: permission: microphone: доступ запрещён в настройках", "Microphone"),
    ],
)
def test_denied_access_becomes_permission_error_with_the_right_service(fake_helper, line, service):
    fake_helper(FakeProcess([line], exit_code=1))
    with pytest.raises(PermissionDeniedError) as caught:
        open_capture("system" if service == "ScreenCapture" else "mic")
    assert caught.value.service == service
    assert "Системные настройки" in str(caught.value)


def test_other_failures_show_their_real_text_not_a_permission_hint(fake_helper):
    fake_helper(FakeProcess(["error: нет дисплея (code -3805)"], exit_code=1))
    with pytest.raises(AudioError) as caught:
        open_capture()
    assert not isinstance(caught.value, PermissionDeniedError)
    assert "нет дисплея (code -3805)" in str(caught.value)
    assert "Запись экрана" not in str(caught.value)  # подсказка про разрешения здесь была бы ложной


def test_helper_that_dies_silently_is_not_blamed_on_permissions(fake_helper):
    fake_helper(FakeProcess([], exit_code=134))
    with pytest.raises(AudioError) as caught:
        open_capture()
    assert not isinstance(caught.value, PermissionDeniedError)
    assert "помощник завершился" in str(caught.value)


def test_working_helper_delivers_audio_chunks(fake_helper):
    samples = np.full(4800, 0.25, dtype=np.float32)
    process = fake_helper(FakeProcess(["ready"], audio=samples.tobytes()))
    out: queue.Queue = queue.Queue()
    capture = audio_mac.AudioSystem().open_capture(audio_mac.SYSTEM_DEVICE, out)
    chunk = out.get(timeout=2)
    assert len(chunk.samples) == 1600 and abs(float(chunk.samples.mean()) - 0.25) < 0.01
    capture.stop()
    assert process.terminated or process.poll() is not None


def test_real_helper_error_text_is_logged(fake_helper, caplog):
    fake_helper(FakeProcess(["error: permission: screen: declined"], exit_code=1))
    with caplog.at_level("WARNING", logger="govorilka.audio_mac"), pytest.raises(PermissionDeniedError):
        open_capture()
    assert "permission: screen: declined" in caplog.text


# ---------- сброс разрешения ----------


def test_reset_runs_tccutil_for_this_app(monkeypatch):
    calls = []
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    assert permissions.reset_permission("ScreenCapture", "io.example.app") is None
    assert calls == [["tccutil", "reset", "ScreenCapture", "io.example.app"]]


def test_reset_failure_returns_the_reason(monkeypatch):
    monkeypatch.setattr(
        permissions.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "No such bundle\n")
    )
    assert permissions.reset_permission("Microphone") == "No such bundle"
    monkeypatch.setattr(permissions.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("tccutil")))
    assert "tccutil" in permissions.reset_permission("Microphone")


def test_reset_is_offered_only_for_the_packaged_mac_app(monkeypatch):
    monkeypatch.setattr(permissions, "FROZEN", True)
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    assert permissions.can_reset()
    monkeypatch.setattr(permissions, "FROZEN", False)  # запуск из исходников: у него нет своего идентификатора
    assert not permissions.can_reset()
    monkeypatch.setattr(permissions, "FROZEN", True)
    monkeypatch.setattr(permissions.sys, "platform", "win32")
    assert not permissions.can_reset()


def test_bundle_id_matches_the_one_in_the_build_spec():
    from govorilka.config import BUNDLE_ID

    spec = (permissions.__file__.rsplit("govorilka", 1)[0] + "govorilka.spec")
    assert f'bundle_identifier="{BUNDLE_ID}"' in open(spec, encoding="utf-8").read()


# ---------- тип ошибки доходит до окна ----------


def test_session_start_keeps_the_permission_error_type(monkeypatch, tmp_path):
    from govorilka import session as session_module
    from govorilka.audio_base import DeviceInfo
    from govorilka.config import Settings

    class Audio:
        def close(self):
            pass

        def default_mic(self):
            return DeviceInfo(1, "mic", 48_000, 1)

        def default_loopback(self):
            raise PermissionDeniedError("ScreenCapture", "Нет доступа к записи системного звука.")

        def open_capture(self, device, out):
            raise AssertionError("до открытия дойти не должно")

        @staticmethod
        def default_signature():
            return None

    class Detector:
        frame_size = 480

        def is_speech(self, frame):
            return False

    monkeypatch.setattr(session_module, "AudioSystem", Audio)
    mic_only_ok = type("Capture", (), {"device": DeviceInfo(1, "mic", 48_000, 1), "is_alive": lambda s: True, "stop": lambda s: None})
    monkeypatch.setattr(Audio, "open_capture", lambda self, device, out: mic_only_ok(), raising=False)
    engines = type(
        "E",
        (),
        {
            "recognizer": None,
            "vad": type("V", (), {"detector": lambda self: Detector()})(),
            "speakers": type("S", (), {"embed": lambda self, a: a})(),
        },
    )()
    session = session_module.Session(Settings(transcripts_dir=str(tmp_path)), engines, "t", lambda e: None, lambda *a: None)
    with pytest.raises(PermissionDeniedError) as caught:
        session.start()
    assert caught.value.service == "ScreenCapture"
