"""Проверки исправлений по ревью: потеря данных и тихие сбои."""

import json
from datetime import datetime

import numpy as np
import pytest

from govorilka.config import Settings, load_settings, save_settings
from govorilka.filejob import FileJobError, decode_audio
from govorilka.pipeline import PhraseProcessor
from govorilka.segmenter import SegmentEvent
from govorilka.transcript import AutoSaver, Transcript, Utterance, unique_path

SR = 16_000


# ---------- имя файла ----------


def test_second_recording_in_the_same_minute_does_not_overwrite_the_first(tmp_path):
    started = datetime(2026, 10, 23, 14, 5)
    first = AutoSaver(Transcript(started, "Планёрка"), tmp_path)
    first.transcript.add(Utterance("Я", 0, "важный текст"))
    first.save()
    second = AutoSaver(Transcript(started, "Планёрка"), tmp_path)  # остановили и сразу начали заново
    second.save()
    assert first.path != second.path and second.path.name.endswith("_2.md")
    assert "важный текст" in first.path.read_text(encoding="utf-8")


def test_unique_path_counts_up(tmp_path):
    base = tmp_path / "a.md"
    base.write_text("x")
    (tmp_path / "a_2.md").write_text("x")
    assert unique_path(base).name == "a_3.md"


def test_transcript_files_use_unix_newlines(tmp_path):
    saver = AutoSaver(Transcript(datetime(2026, 1, 1), "t"), tmp_path)
    saver.transcript.add(Utterance("Я", 0, "a"))
    saver.save()
    assert b"\r\n" not in saver.path.read_bytes()


# ---------- сохранение видно ----------


def test_save_failure_is_reported_once_and_recovery_too(tmp_path):
    states = []
    blocker = tmp_path / "blocked"
    blocker.write_text("файл на месте папки", encoding="utf-8")  # mkdir внутри упадёт
    saver = AutoSaver(Transcript(datetime(2026, 1, 1), "t"), blocker, states.append)
    assert saver.save() is None and saver.save() is None
    assert len(states) == 1 and states[0]  # текст ошибки, один раз
    blocker.unlink()
    assert saver.save() is not None
    assert states[-1] is None and len(states) == 2


# ---------- настройки ----------


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe[\x00]\x00", "[]".encode(), b"null", b"42", "{не json".encode()],
)
def test_strange_settings_files_do_not_break_startup(tmp_path, raw):
    path = tmp_path / "s.json"
    path.write_bytes(raw)
    assert load_settings(path) == Settings()


def test_settings_with_bom_and_wrong_types(tmp_path):
    path = tmp_path / "s.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"capture_mic": "yes", "echo_filter": False}).encode())
    loaded = load_settings(path)
    assert loaded.echo_filter is False  # нормальное поле принято
    assert loaded.capture_mic is True  # строка вместо флага отброшена


def test_save_settings_leaves_no_temp_file(tmp_path):
    path = tmp_path / "s.json"
    save_settings(Settings(), path)
    assert path.exists() and not list(tmp_path.glob("*.tmp"))


# ---------- ошибки распознавания ----------


class BrokenRecognizer:
    def transcribe(self, audio):
        raise RuntimeError("onnx упал")


class GoodRecognizer:
    def transcribe(self, audio):
        return "текст"


def _final(seconds=2.0):
    return SegmentEvent("final", np.zeros(int(SR * seconds), dtype=np.float32), 0.0)


def test_repeated_recognition_failures_are_reported_not_silent():
    problems, events = [], []
    processor = PhraseProcessor("mic", "Я", BrokenRecognizer(), events.append, on_problem=problems.append)
    for _ in range(6):
        processor.handle(_final())
    assert [e.kind for e in events] == ["discard"] * 6
    assert len(problems) == 2  # на 1-й и 5-й, а не на каждой
    assert "Пропущено подряд: 5" in problems[1]


def test_failure_counter_resets_after_success():
    problems = []
    processor = PhraseProcessor("mic", "Я", BrokenRecognizer(), lambda e: None, on_problem=problems.append)
    processor.handle(_final())
    processor._recognizer = GoodRecognizer()
    processor.handle(_final())
    processor._recognizer = BrokenRecognizer()
    processor.handle(_final())
    assert len(problems) == 2  # после успеха снова «первая» ошибка


def test_diarizer_failure_keeps_the_text():
    class BrokenDiarizer:
        def assign(self, audio):
            raise RuntimeError("embedding упал")

    problems, events = [], []
    processor = PhraseProcessor(
        "system", "Собеседник", GoodRecognizer(), events.append, BrokenDiarizer(), on_problem=problems.append
    )
    processor.handle(_final())
    processor.handle(_final())
    assert [(e.kind, e.text, e.speaker) for e in events] == [("final", "текст", "Собеседник")] * 2
    assert len(problems) == 1  # о сбое голосов говорим один раз


# ---------- файл закрывается ----------


def test_decode_audio_close_releases_the_file_even_if_never_read(tmp_path):
    import wave

    path = tmp_path / "a.wav"
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.zeros(SR, dtype=np.int16).tobytes())
    audio = decode_audio(path)
    audio.close()  # чтение не начинали
    audio.close()  # повторный вызов безопасен
    path.unlink()  # на Windows упало бы, если файл остался открыт
    assert not path.exists()


def test_decode_audio_without_track_raises_clear_error(tmp_path):
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"\x00" * 64)
    with pytest.raises(FileJobError):
        decode_audio(bad)
