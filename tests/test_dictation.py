"""Диктовка в любое поле: горячая клавиша, запись по фразам, контроллер и вставка у курсора."""

import threading
import time

import numpy as np
import pytest

from govorilka.audio_base import AudioChunk, DeviceInfo
from govorilka.dictation import Dictation, join_phrases
from govorilka.hotkeys import HotkeyWatcher, KeySnapshot, parse_chord
from govorilka.vad import EnergyDetector


def snap(mods="", keys=""):
    return KeySnapshot(frozenset(m for m in mods.split("+") if m), frozenset(k for k in keys.split("+") if k))


class ScriptedKeyboard:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)

    def snapshot(self):
        return self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def run_watcher(snapshots):
    events = []
    clock = iter(x * 0.2 for x in range(10_000))
    watcher = HotkeyWatcher(
        lambda: events.append("menu"),
        keyboard=ScriptedKeyboard(snapshots),
        clock=lambda: next(clock),
        on_dictate_start=lambda: events.append("start"),
        on_dictate_stop=lambda cancelled: events.append("cancel" if cancelled else "stop"),
    )
    watcher.set_dictation_chord(parse_chord("ctrl+alt"))
    watcher.start()
    wait_for(lambda: len(events) >= 2, timeout=1.5)
    time.sleep(0.2)
    watcher.stop()
    return events


# ---------- горячая клавиша ----------


def test_dictation_starts_after_a_short_hold_and_stops_when_one_key_is_released():
    events = run_watcher([snap("ctrl+alt")] * 3 + [snap("ctrl")] * 3 + [snap()])
    assert events == ["start", "stop"]  # отпустили одну клавишу — диктовка кончилась, не дожидаясь второй


def test_typing_while_holding_the_chord_cancels_dictation():
    events = run_watcher([snap("ctrl+alt")] * 3 + [snap("ctrl+alt", "c")] * 2 + [snap()])
    assert events == ["start", "cancel"]  # Ctrl+Alt+C — это сочетание, а не диктовка: текст не вставляем


def test_a_quick_tap_does_not_start_dictation():
    events = run_watcher([snap("ctrl+alt"), snap()] * 3)
    assert events == []


# ---------- запись по фразам ----------


class FakeRecognizer:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, skip_if_busy=False):
        self.calls.append(len(audio))
        return f"фраза {len(self.calls)}."


class FakeVad:
    def detector(self):
        return EnergyDetector()


class FakeEngines:
    def __init__(self):
        self.recognizer = FakeRecognizer()
        self.vad = FakeVad()


class FakeCapture:
    def __init__(self, out, script):
        self.stopped = threading.Event()
        self._thread = threading.Thread(target=self._play, args=(out, script), daemon=True)
        self._thread.start()

    def _play(self, out, script):
        t = 0.0
        for chunk in script:
            if self.stopped.is_set():
                return
            out.put(AudioChunk(chunk, t))
            t += len(chunk) / 16_000

    def stop(self):
        self.stopped.set()

    def is_alive(self):
        return not self.stopped.is_set()


class FakeAudio:
    def __init__(self, script):
        self.script = script
        self.capture = None

    def default_mic(self):
        return DeviceInfo(0, "mic", 16_000, 1)

    def open_capture(self, device, out):
        self.capture = FakeCapture(out, self.script)
        return self.capture


def speech_script(*parts):
    """Куски по 0.1 с: «говорим» громким шумом, «молчим» тишиной."""
    rng = np.random.default_rng(1)
    chunks = []
    for kind, seconds in parts:
        for _ in range(int(seconds * 10)):
            if kind == "speech":
                chunks.append((rng.standard_normal(1600) * 0.2).astype(np.float32))
            else:
                chunks.append(np.zeros(1600, dtype=np.float32))
    return chunks


def test_phrases_are_recognised_while_speaking_and_the_last_one_after_release():
    engines = FakeEngines()
    audio = FakeAudio(speech_script(("silence", 0.5), ("speech", 1.5), ("silence", 1.2), ("speech", 1.0)))
    levels = []
    dictation = Dictation(engines, audio, on_level=levels.append)
    dictation.start()
    assert wait_for(lambda: len(engines.recognizer.calls) == 1)  # первая фраза распознана, пока ещё говорим
    time.sleep(0.4)
    text = dictation.finish()  # вторая фраза оборвана отпусканием: её дораспознаём
    assert text == "фраза 1. фраза 2."
    assert max(levels) > 0.1 and audio.capture.stopped.is_set()


def test_silence_gives_empty_text():
    dictation = Dictation(FakeEngines(), FakeAudio(speech_script(("silence", 1.0))))
    dictation.start()
    time.sleep(0.3)
    assert dictation.finish() == ""


def test_dictation_stops_itself_at_the_limit():
    limited = threading.Event()
    dictation = Dictation(FakeEngines(), FakeAudio(speech_script(("speech", 3.0))), max_s=0.5, on_limit=limited.set)
    dictation.start()
    assert limited.wait(3)
    dictation.cancel()


def test_join_phrases_skips_empty_pieces():
    assert join_phrases(["Привет.", "", "  ", "Как дела?"]) == "Привет. Как дела?"


# ---------- контроллер ----------


class Listener:
    def __init__(self):
        self.phases, self.texts, self.errors = [], [], []

    def __getattr__(self, name):
        return lambda *a, **k: None

    def on_dictation(self, phase):
        self.phases.append(phase)

    def on_dictation_text(self, text):
        self.texts.append(text)

    def on_error(self, text):
        self.errors.append(text)


class NoHotkeys:
    def set_chord(self, chord): ...
    def start(self): ...
    def stop(self): ...


class StubDictation:
    created = []

    def __init__(self, engines, on_level=None, on_limit=None):
        self.finished = self.cancelled = False
        StubDictation.created.append(self)

    def start(self):
        time.sleep(0.05)

    def finish(self):
        self.finished = True
        return "Купить хлеб."

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def controller(tmp_path, monkeypatch):
    from govorilka import controller as controller_module
    from govorilka.config import Settings

    StubDictation.created = []
    monkeypatch.setattr(controller_module, "Dictation", StubDictation)
    listener = Listener()
    c = controller_module.Controller(listener, Settings(transcripts_dir=str(tmp_path)), hotkeys=NoHotkeys())
    monkeypatch.setattr(c, "_ensure_engines", lambda: object())
    return c, listener


def test_controller_hands_the_text_over_after_release(controller):
    c, listener = controller
    c.start_dictation()
    assert wait_for(lambda: "listening" in listener.phases)
    c.stop_dictation()
    assert wait_for(lambda: listener.texts == ["Купить хлеб."])
    assert wait_for(lambda: listener.phases[-1] == "")
    assert "recognizing" in listener.phases and c._dictation_run is None


def test_release_before_the_microphone_opened_still_gives_text(controller):
    c, listener = controller
    c.start_dictation()
    c.stop_dictation()  # отпустили сразу, микрофон ещё открывался
    assert wait_for(lambda: listener.texts == ["Купить хлеб."])


def test_typing_cancels_without_pasting(controller):
    c, listener = controller
    c.start_dictation()
    assert wait_for(lambda: "listening" in listener.phases)
    c.stop_dictation(cancelled=True)
    assert wait_for(lambda: listener.phases[-1] == "")
    assert listener.texts == [] and StubDictation.created[0].cancelled


def test_models_are_not_unloaded_while_dictating(controller):
    c, listener = controller
    c.engines = object()
    c.start_dictation()
    assert wait_for(lambda: "listening" in listener.phases)
    assert c.unload_models() is False
    c.stop_dictation()
    assert wait_for(lambda: listener.phases[-1] == "")


def test_dictation_with_the_same_chord_as_recording_is_refused(tmp_path):
    from govorilka.config import Settings
    from govorilka.controller import Controller

    class Hotkeys(NoHotkeys):
        dictation = "unset"

        def set_dictation_chord(self, chord):
            Hotkeys.dictation = chord

    listener = Listener()
    statuses = []
    listener.on_status = statuses.append
    Controller(listener, Settings(transcripts_dir=str(tmp_path), hotkey="ctrl+alt", dictation_hotkey="alt+ctrl"), hotkeys=Hotkeys())
    assert Hotkeys.dictation is None and "та же комбинация" in statuses[-1]
    Controller(listener, Settings(transcripts_dir=str(tmp_path), hotkey="ctrl+shift", dictation_hotkey="ctrl+alt"), hotkeys=Hotkeys())
    assert Hotkeys.dictation == parse_chord("ctrl+alt")

