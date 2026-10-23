"""Контроллер приложения: состояния, запись, пауза, отметки — без интерфейса и без звука."""

import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from govorilka import controller as controller_module
from govorilka.audio_base import PermissionDeniedError
from govorilka.config import Settings
from govorilka.controller import AppState, Controller, Listener
from govorilka.transcript import Transcript


class Recorder(Listener):
    def __init__(self):
        self.states, self.errors, self.statuses, self.finished, self.permissions = [], [], [], [], []

    def on_state(self, state):
        self.states.append(state)

    def on_error(self, text):
        self.errors.append(text)

    def on_status(self, text):
        self.statuses.append(text)

    def on_finished(self, path, message):
        self.finished.append((path, message))

    def on_permission(self, error):
        self.permissions.append(error)


class FakeHotkeys:
    def __init__(self):
        self.chord = "unset"

    def set_chord(self, chord):
        self.chord = chord

    def start(self):
        pass

    def stop(self):
        pass


class FakeSession:
    created: list = []
    fail_with: Exception | None = None
    gate = threading.Event()  # пока не открыт, запись «ещё стартует»

    def __init__(self, settings, engines, title, on_text, on_level, on_notice):
        self.transcript = Transcript(datetime(2026, 1, 1), title)
        self.path = Path(settings.transcripts_dir) / "call.md"
        self.paused = False
        self.marks = 0
        self.stopped = threading.Event()
        FakeSession.created.append(self)

    def start(self):
        FakeSession.gate.wait(2)
        if FakeSession.fail_with:
            raise FakeSession.fail_with

    def stop(self):
        time.sleep(0.05)  # дописывание последних фраз
        self.stopped.set()
        return self.path

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def add_mark(self):
        self.marks += 1

    def elapsed(self):
        return 12.0

    def diarizers(self):
        return {}

    saver = type("Saver", (), {"save": staticmethod(lambda: None)})()

    def save(self):
        return self.path


def wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def app(monkeypatch, tmp_path):
    FakeSession.created, FakeSession.fail_with = [], None
    FakeSession.gate.set()
    monkeypatch.setattr(controller_module, "Session", FakeSession)
    monkeypatch.setattr(controller_module, "load_engines", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "save_settings", lambda s: None)
    listener = Recorder()
    return Controller(listener, Settings(transcripts_dir=str(tmp_path)), hotkeys=FakeHotkeys()), listener


def test_full_recording_cycle(app):
    c, events = app
    assert c.start_recording("Планёрка")
    assert wait_for(lambda: c.state == AppState.RECORDING)
    c.toggle_pause()
    assert c.state == AppState.PAUSED and FakeSession.created[0].paused
    c.toggle_pause()
    assert c.state == AppState.RECORDING and not FakeSession.created[0].paused
    assert c.add_mark() and FakeSession.created[0].marks == 1
    c.stop_recording()
    assert c.state == AppState.STOPPING
    assert wait_for(lambda: c.state == AppState.IDLE)
    assert events.states == [AppState.STARTING, AppState.RECORDING, AppState.PAUSED, AppState.RECORDING, AppState.STOPPING, AppState.IDLE]
    assert events.finished and events.finished[0][0].name == "call.md"


def test_double_start_and_toggle_during_start_are_ignored(app):
    c, _ = app
    FakeSession.gate.clear()
    assert c.start_recording()
    assert c.state == AppState.STARTING
    assert not c.start_recording()  # второй запуск, пока первый ещё стартует
    c.toggle_recording()  # горячая клавиша во время запуска не должна останавливать и не должна запускать вторую
    FakeSession.gate.set()
    assert wait_for(lambda: c.state == AppState.RECORDING)
    assert len(FakeSession.created) == 1


def test_toggle_recording_starts_and_stops(app):
    c, _ = app
    c.toggle_recording()
    assert wait_for(lambda: c.state == AppState.RECORDING)
    c.toggle_recording()
    assert wait_for(lambda: c.state == AppState.IDLE)


def test_pause_and_mark_without_recording_do_nothing(app):
    c, _ = app
    c.toggle_pause()
    assert c.state == AppState.IDLE
    assert c.add_mark() is False


def test_permission_denial_goes_to_its_own_event(app):
    c, events = app
    FakeSession.fail_with = PermissionDeniedError("ScreenCapture", "Нет доступа")
    c.start_recording()
    assert wait_for(lambda: events.permissions)
    assert c.state == AppState.IDLE and events.permissions[0].service == "ScreenCapture"
    assert events.errors == []


def test_other_start_errors_are_reported(app):
    from govorilka.audio import AudioError

    c, events = app
    FakeSession.fail_with = AudioError("Микрофон не найден")
    c.start_recording()
    assert wait_for(lambda: events.errors)
    assert events.errors == ["Микрофон не найден"] and c.state == AppState.IDLE


def test_send_to_claude_without_transcript_explains_why(app):
    c, _ = app
    result = c.send_to_claude()
    assert not result.ok and "Сначала запишите" in result.message


def test_send_to_claude_without_cli_offers_the_prompt(app, tmp_path, monkeypatch):
    c, _ = app
    c.last_file = tmp_path / "call.md"
    c.last_file.write_text("# Созвон", encoding="utf-8")

    def missing(*a, **k):
        raise controller_module.ClaudeNotFoundError("Claude Code не найден")

    monkeypatch.setattr(controller_module, "open_in_claude", missing)
    result = c.send_to_claude()
    assert not result.ok and str(c.last_file) in result.clipboard


def test_save_as_without_text_refuses(app, tmp_path):
    c, _ = app
    with pytest.raises(ValueError):
        c.save_as(tmp_path / "x.md")


def test_hotkey_follows_settings(app):
    c, _ = app
    c.update_settings(c.settings.__class__(hotkey_enabled=False))
    assert c.hotkeys.chord is None
    c.update_settings(c.settings.__class__(hotkey="ctrl+alt+r"))
    assert c.hotkeys.chord.key == "r"


def test_shutdown_waits_for_the_tail_of_the_recording(app):
    c, _ = app
    c.start_recording()
    assert wait_for(lambda: c.state == AppState.RECORDING)
    session = FakeSession.created[0]
    c.shutdown()
    assert session.stopped.is_set()


def test_a_failing_after_hook_never_leaves_the_app_stuck_in_stopping(app, monkeypatch):
    c, events = app
    monkeypatch.setattr(c, "_after_recording", lambda session: (_ for _ in ()).throw(RuntimeError("упало")))
    c.start_recording()
    assert wait_for(lambda: c.state == AppState.RECORDING)
    c.stop_recording()
    assert wait_for(lambda: c.state == AppState.IDLE)
    assert wait_for(lambda: events.errors)  # пользователь узнаёт об ошибке, а не видит вечное «Сохраняю…»


def test_name_given_after_the_call_reaches_the_file(app, tmp_path):
    from datetime import datetime

    from govorilka.transcript import Transcript, Utterance

    c, _ = app
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(Utterance("Собеседник 1", 1.0, "привет", 2.0, "system", "Собеседник 1"))
    c.transcript, c.last_file = transcript, tmp_path / "call.md"
    assert c.rename_speaker("Собеседник 1", "Аня") == 1
    assert "Аня" in c.last_file.read_text(encoding="utf-8")


def test_renaming_a_label_that_no_longer_exists_changes_nothing():
    from datetime import datetime

    from govorilka.transcript import Transcript, Utterance

    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(Utterance("Собеседник 1", 1.0, "привет", 2.0, "system", "Собеседник 1"))
    assert transcript.rename_voice("Собеседник 7", "Аня") == 0
    assert transcript.aliases == {} and transcript.user_named == set()
