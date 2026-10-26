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
    monkeypatch.setattr(controller_module, "save_settings", lambda s: True)
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


def test_models_are_unloaded_when_idle_and_loaded_again_on_demand(app, monkeypatch):
    from govorilka import controller as controller_module

    c, _ = app
    c.engines = object()
    assert c.unload_models() is True and c.engines is None  # простой: выгрузили
    c.engines = object()
    c.state = AppState.RECORDING
    assert c.unload_models() is False and c.engines is not None  # во время записи модели трогать нельзя
    c.state = AppState.IDLE


def test_unload_is_scheduled_after_a_recording_and_cancelled_by_the_next_one(app):
    c, _ = app
    c.engines = object()
    c.settings = c.settings.__class__(unload_after_min=10)
    c._schedule_unload()
    assert c._unload_timer is not None and c._unload_timer.interval == 600
    c._cancel_unload()
    assert c._unload_timer is None
    c.settings = c.settings.__class__(unload_after_min=0)  # «никогда»
    c._schedule_unload()
    assert c._unload_timer is None


def test_unloading_asks_the_interface_for_garbage_collection_instead_of_doing_it_in_a_timer_thread(app):
    c, events = app
    calls = []
    events.on_models_unloaded = lambda: calls.append(1)
    c.engines = object()
    assert c.unload_models() and calls == [1]


# ---------- QA: остановка, сохранение, подсказки, итоги ----------


def test_stop_reports_when_the_final_save_failed(app, monkeypatch):
    c, events = app
    monkeypatch.setattr(FakeSession, "stop", lambda self: None)  # диск полон: сохранить не удалось
    assert c.start_recording()
    assert wait_for(lambda: c.state == AppState.RECORDING)
    c.stop_recording()
    assert wait_for(lambda: c.state == AppState.IDLE)
    assert not events.finished and any("не сохранился" in e for e in events.errors)


def test_hints_are_not_left_running_when_the_recording_stops_while_starting(app, monkeypatch):
    c, _ = app
    started = []
    monkeypatch.setattr(Controller, "_start_assistant", lambda self: started.append(self.session))
    c.settings = Settings(transcripts_dir=c.settings.transcripts_dir, assistant_enabled=True)
    FakeSession.gate.clear()
    assert c.start_recording()
    original_set_state = c._set_state

    def stop_right_after_recording(state):
        original_set_state(state)
        if state == AppState.RECORDING:
            c.stop_recording()  # остановили ровно между «запись идёт» и запуском подсказок

    c._set_state = stop_right_after_recording
    FakeSession.gate.set()
    assert wait_for(lambda: c.state == AppState.IDLE)
    assert started == []


def test_same_chord_is_found_whatever_the_spelling():
    from govorilka.controller import _same_chord
    from govorilka.hotkeys import parse_chord

    assert _same_chord("Shift+Ctrl", parse_chord("ctrl + shift"))
    assert not _same_chord("ctrl+alt", parse_chord("ctrl+shift"))
    assert not _same_chord("бред", parse_chord("ctrl+shift"))


def test_summary_goes_into_its_own_file_even_if_a_new_recording_started(app, monkeypatch, tmp_path):
    from govorilka import summary
    from govorilka.transcript import Transcript, Utterance

    c, _ = app
    old = Transcript(datetime_now(), "Старый")
    old.add(Utterance("Я", 1.0, "Релиз в пятницу", 2.0, "mic"))
    c.transcript, c.last_file = old, tmp_path / "old.md"

    class Server:
        url, api_key = "http://127.0.0.1:1", "k"

        def __init__(self, *a):
            pass

        def wait_ready(self, **k):
            c.transcript, c.last_file = object(), tmp_path / "new.md"  # пока считали, началась новая запись

        def stop(self):
            pass

    monkeypatch.setattr(summary, "ensure_engine", lambda *a, **k: tmp_path)
    monkeypatch.setattr(summary, "ensure_model", lambda *a, **k: tmp_path)
    monkeypatch.setattr(summary, "LlamaServer", Server)
    monkeypatch.setattr(summary, "fit", lambda *a: ("ok", ""))
    monkeypatch.setattr(summary, "summarize", lambda *a, **k: "### Кратко\nРелиз в пятницу.")
    c.make_summary(lambda _s: None)
    assert "Релиз в пятницу." in (tmp_path / "old.md").read_text(encoding="utf-8")
    assert not (tmp_path / "new.md").exists()


def test_cancelled_summary_is_not_saved(app, monkeypatch, tmp_path):
    from govorilka import summary
    from govorilka.transcript import Transcript, Utterance

    c, _ = app
    talk = Transcript(datetime_now(), "")
    talk.add(Utterance("Я", 1.0, "Текст", 2.0, "mic"))
    c.transcript, c.last_file = talk, tmp_path / "t.md"
    cancel = threading.Event()

    class Server:
        def __init__(self, *a):
            pass

        def wait_ready(self, **k):
            pass

        def stop(self):
            pass

    def summarize(*a, **k):
        cancel.set()  # нажали «Отменить», пока модель отвечала
        return "### Кратко\nНе должно сохраниться."

    monkeypatch.setattr(summary, "ensure_engine", lambda *a, **k: tmp_path)
    monkeypatch.setattr(summary, "ensure_model", lambda *a, **k: tmp_path)
    monkeypatch.setattr(summary, "LlamaServer", Server)
    monkeypatch.setattr(summary, "fit", lambda *a: ("ok", ""))
    monkeypatch.setattr(summary, "summarize", summarize)
    with pytest.raises(summary.SummaryError, match="Отменено"):
        c.make_summary(lambda _s: None, cancel.is_set)
    assert not (tmp_path / "t.md").exists() and not talk.summary


def datetime_now():
    from datetime import datetime

    return datetime.now()
