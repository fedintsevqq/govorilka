"""Фаза 3: уточнение подписей говорящих после созвона, имена и память голосов."""

import json
import wave
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from govorilka.refine import refine_transcript, remember_names
from govorilka.speakers import (
    MAX_SPEAKERS,
    OFFLINE_SAME,
    Diarizer,
    Sample,
    cluster_centroids,
    cluster_samples,
)
from govorilka.transcript import SOURCE_MARK, Transcript, Utterance
from govorilka.voices import RECOGNIZE_AT, VoiceBook

SR = 16_000
FIXTURES = Path(__file__).parent / "fixtures"
RNG = np.random.default_rng(7)


def unit(vector):
    return vector / np.linalg.norm(vector)


def base_voices(count=4, dim=256):
    return [unit(RNG.normal(0, 1, dim)) for _ in range(count)]


def noisy(base, noise=0.75):
    """Тот же человек в другой фразе. noise — длина «шума» относительно голоса: 0.75 даёт близость около 0.8, как у настоящих голосов."""
    return unit(base + RNG.normal(0, noise / np.sqrt(base.size), base.shape))


def partly_like(base, weight=1.6):
    """Вектор, лишь отчасти похожий на base (близость ≈ 0.5): осколок чужой речи или помеха."""
    other = RNG.normal(0, 1, base.shape)
    other -= base * float(other @ base)
    return unit(base + weight * unit(other))


# ---------- кластеризация ----------


def test_clustering_finds_the_right_number_of_people_in_any_order():
    a, b, c = base_voices(3)
    order = [a, a, b, a, b, c, b, a, c, c, a, b]
    labels = cluster_samples([noisy(v) for v in order], [4.0] * len(order))
    expected = {id(a): 0, id(b): 1, id(c): 2}
    assert labels == [expected[id(v)] for v in order]  # нумерация по порядку первого появления


def test_clustering_edge_cases():
    assert cluster_samples([], []) == []
    assert cluster_samples([base_voices(1)[0]], [3.0]) == [0]
    same = base_voices(1)[0]
    assert cluster_samples([same, same, same], [2.0] * 3) == [0, 0, 0]


def test_a_short_shard_does_not_become_a_new_person():
    a, b = base_voices(2)
    vectors = [noisy(a), noisy(b), noisy(a), noisy(b), noisy(a)]
    seconds = [4.0] * 5
    odd = partly_like(a)  # похож на «a», но слабо; 1.1 с речи
    assert cluster_samples(vectors + [odd], seconds + [1.1])[-1] == 0


def test_a_long_dissimilar_voice_is_a_new_person():
    a, b = base_voices(2)
    labels = cluster_samples([noisy(a), noisy(b)], [8.0, 8.0])
    assert labels == [0, 1]


def test_never_more_than_the_maximum_number_of_speakers():
    voices = base_voices(MAX_SPEAKERS + 5)
    labels = cluster_samples(voices, [6.0] * len(voices))
    assert len(set(labels)) <= MAX_SPEAKERS


def test_centroids_weigh_long_phrases_more():
    a, b = base_voices(2)
    centroid = cluster_centroids([a, b], [9.0, 1.0], [0, 0])[0]
    assert float(centroid @ a) > float(centroid @ b)


# ---------- отпечатки по ходу записи ----------


def test_diarizer_keeps_history_only_for_long_phrases():
    voices = iter(base_voices(2))
    diarizer = Diarizer(embed=lambda audio: next(voices))
    diarizer.assign(np.zeros(SR * 2, np.float32), key=1.0)
    diarizer.assign(np.zeros(SR // 2, np.float32), key=5.0)  # короткое «да» — отпечатка нет
    diarizer.assign(np.zeros(SR * 3, np.float32), key=9.0)
    assert [s.key for s in diarizer.history] == [1.0, 9.0]
    assert [round(s.seconds) for s in diarizer.history] == [2, 3]


def test_diarizer_without_key_keeps_no_history():
    diarizer = Diarizer(embed=lambda audio: base_voices(1)[0])
    diarizer.assign(np.zeros(SR * 2, np.float32))
    assert diarizer.history == []


# ---------- уточнение транскрипта ----------


def make_call(layout, voices, online_labels):
    """layout: [(время, источник, текст, номер голоса или None)]. Возвращает транскрипт и диаризатор «как после записи»."""
    transcript = Transcript(datetime(2026, 1, 1), "Созвон")
    diarizer = Diarizer(embed=lambda audio: voices[0])
    for start, source, text, voice in layout:
        if source == "system":
            label = f"Собеседник {online_labels[len(diarizer.history) if voice is not None else -1]}" if voice is not None else None
        if source == "mic":
            transcript.add(Utterance("Я", start, text, start + 3, "mic", "Я"))
        elif source == SOURCE_MARK:
            transcript.add(Utterance("", start, text, start, SOURCE_MARK))
        elif voice is None:  # короткая реплика без отпечатка: по ходу записи получила подпись предыдущей
            transcript.add(Utterance("Собеседник 1", start, text, start + 0.5, "system", "Собеседник 1"))
        else:
            diarizer.history.append(Sample(start, noisy(voices[voice]), 4.0, int(label.split()[-1])))
            transcript.add(Utterance(label, start, text, start + 4, "system", label))
    return transcript, diarizer


def test_refine_merges_a_person_who_got_two_numbers_and_renumbers_by_appearance():
    a, b = base_voices(2)
    layout = [(0, "system", "раз", 0), (10, "system", "два", 1), (20, "system", "три", 0), (30, "system", "четыре", 1), (40, "system", "пять", 0)]
    # по ходу записи «a» получил номера 1, 3, 1 (первая фраза не узнана), «b» — 2 и 2
    transcript, diarizer = make_call(layout, [a, b], online_labels=[1, 2, 3, 2, 1])
    result = refine_transcript(transcript, {"system": diarizer}, {"system": "Собеседник"})
    assert [u.speaker for u in transcript.utterances] == ["Собеседник 1", "Собеседник 2", "Собеседник 1", "Собеседник 2", "Собеседник 1"]
    assert result.speakers == 2 and result.changed == 1  # поменялась подпись у одной реплики


def test_refine_leaves_microphone_marks_and_notes_alone_and_short_replies_follow_the_previous_speaker():
    a, b = base_voices(2)
    layout = [
        (0, "system", "длинная A", 0),
        (6, "mic", "это я", None),
        (9, "system", "да", None),  # короткая реплика без отпечатка
        (12, "system", "длинная B", 1),
        (16, SOURCE_MARK, "Баги", None),
        (18, "system", "ага", None),
    ]
    transcript, diarizer = make_call(layout, [a, b], online_labels=[1, 2])
    refine_transcript(transcript, {"system": diarizer}, {"system": "Собеседник"})
    by_text = {u.text: u for u in transcript.utterances}
    assert by_text["это я"].speaker == "Я" and by_text["Баги"].source == SOURCE_MARK
    assert by_text["да"].speaker == "Собеседник 1"  # продолжает A
    assert by_text["ага"].speaker == "Собеседник 2"  # продолжает B


def test_names_given_during_the_call_survive_renumbering():
    a, b = base_voices(2)
    layout = [(0, "system", "раз", 0), (10, "system", "два", 1), (20, "system", "три", 0), (30, "system", "четыре", 1)]
    # по ходу записи: A получил 1, потом 3 (не узнан), B — 2; пользователь подписал B как «Аня»
    transcript, diarizer = make_call(layout, [a, b], online_labels=[1, 2, 3, 2])
    transcript.rename_voice("Собеседник 2", "Аня")
    refine_transcript(transcript, {"system": diarizer}, {"system": "Собеседник"})
    speakers = [u.speaker for u in transcript.utterances]
    assert speakers == ["Собеседник 1", "Аня", "Собеседник 1", "Аня"]
    assert transcript.aliases == {"Собеседник 2": "Аня"}


def test_nothing_changes_when_there_are_no_voice_samples():
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(Utterance("Собеседник 1", 0, "текст", 3, "system", "Собеседник 1"))
    result = refine_transcript(transcript, {"system": Diarizer(embed=lambda a: a)}, {"system": "Собеседник"})
    assert result.speakers == 0 and transcript.utterances[0].speaker == "Собеседник 1"


def test_voice_memory_names_known_people_and_never_gives_one_name_to_two():
    a, b = base_voices(2)
    layout = [(0, "system", "раз", 0), (10, "system", "два", 1), (20, "system", "три", 0)]
    transcript, diarizer = make_call(layout, [a, b], online_labels=[1, 2, 1])
    book = VoiceBook(Path("нет-файла.json"))
    book.remember("Ирина", a)
    book.remember("Ирина", b)  # то же имя: усредняется, одно имя на двоих не должно достаться обоим
    result = refine_transcript(transcript, {"system": diarizer}, {"system": "Собеседник"}, book)
    named = [u.speaker for u in transcript.utterances]
    assert named.count("Ирина") in (0, 2) and len({s for s in named if s == "Ирина"}) <= 1
    assert len(result.recognized) <= 1


# ---------- переименование ----------


def test_rename_voice_changes_every_reply_and_ignores_empty_names():
    transcript = Transcript(datetime(2026, 1, 1), "т")
    for i in range(3):
        transcript.add(Utterance("Собеседник 2", i * 5, f"р{i}", i * 5 + 2, "system", "Собеседник 2"))
    transcript.add(Utterance("Собеседник 1", 20, "чужая", 22, "system", "Собеседник 1"))
    assert transcript.rename_voice("Собеседник 2", "  Аня ") == 3
    assert [u.speaker for u in transcript.utterances] == ["Аня", "Аня", "Аня", "Собеседник 1"]
    assert transcript.rename_voice("Собеседник 2", "   ") == 0
    assert transcript.rename_voice("Собеседник 2", "Аня") == 0  # уже так названо
    assert "Аня:" in transcript.to_markdown() and "Собеседник 1:" in transcript.to_markdown()


def test_rename_twice_keeps_the_voice_id():
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(Utterance("Собеседник 2", 0, "р", 2, "system", "Собеседник 2"))
    transcript.rename_voice("Собеседник 2", "Аня")
    transcript.rename_voice("Собеседник 2", "Анна")
    assert transcript.utterances[0].speaker == "Анна" and transcript.utterances[0].voice_id == "Собеседник 2"


# ---------- память голосов ----------


def test_voicebook_recognizes_remembers_and_forgets(tmp_path):
    a, b = base_voices(2)
    book = VoiceBook(tmp_path / "voices.json")
    assert book.match(a) is None
    book.remember("Аня", a)
    book.remember("Борис", b)
    book.save()
    again = VoiceBook(tmp_path / "voices.json")
    assert again.match(noisy(a, 0.1))[0] == "Аня" and again.match(noisy(b, 0.1))[0] == "Борис"
    assert again.match(base_voices(1)[0]) is None  # чужой голос не принимаем за знакомого
    assert again.forget("аня") == 1 and again.names == ["Борис"]  # без учёта регистра
    assert again.forget() == 1 and not (tmp_path / "voices.json").exists()


def test_voicebook_averages_the_same_name_instead_of_duplicating(tmp_path):
    a = base_voices(1)[0]
    book = VoiceBook(tmp_path / "v.json")
    for _ in range(3):
        book.remember("Аня", noisy(a))
    assert book.names == ["Аня"] and book.voices[0].count == 3
    assert float(book.voices[0].vector @ a) > 0.88  # усреднение возвращает голос к «настоящему» (у одной записи ≈ 0.8)


def test_voicebook_stores_only_numbers_not_audio(tmp_path):
    book = VoiceBook(tmp_path / "v.json")
    book.remember("Аня", base_voices(1)[0])
    book.save()
    data = json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))
    assert set(data["voices"][0]) == {"name", "count", "vector"} and len(data["voices"][0]["vector"]) == 256


def test_broken_voicebook_does_not_stop_the_app(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{битый", encoding="utf-8")
    assert VoiceBook(path).voices == []
    assert list(tmp_path.glob("v*.broken"))


def test_remember_names_saves_only_people_with_real_names(tmp_path):
    a, b = base_voices(2)
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.aliases = {"Собеседник 1": "Аня", "Собеседник 2": "Собеседник 2"}
    transcript.centroids = {"Собеседник 1": a, "Собеседник 2": b}
    transcript.user_named = {"Собеседник 1"}
    book = VoiceBook(tmp_path / "v.json")
    assert remember_names(transcript, book) == 1 and book.names == ["Аня"]


def test_recognition_threshold_is_stricter_than_the_in_call_threshold():
    assert RECOGNIZE_AT > OFFLINE_SAME  # узнать «знакомого» из памяти надо увереннее, чем сказать «тот же человек в этом созвоне»


# ---------- контроллер ----------


def make_controller(tmp_path, monkeypatch, remember=False):
    from govorilka import controller as controller_module
    from govorilka.config import Settings
    from govorilka.controller import Controller, Listener

    monkeypatch.setattr(controller_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(controller_module, "save_settings", lambda s: True)

    class Hotkeys:
        def set_chord(self, c): ...
        def start(self): ...
        def stop(self): ...

    class Events(Listener):
        def __init__(self):
            self.statuses, self.transcripts, self.errors = [], [], []

        def on_error(self, text):
            self.errors.append(text)

        def on_status(self, text):
            self.statuses.append(text)

        def on_transcript(self, transcript, path):
            self.transcripts.append(transcript)

    events = Events()
    return Controller(events, Settings(transcripts_dir=str(tmp_path), remember_voices=remember), hotkeys=Hotkeys()), events


def test_rename_speaker_in_finished_call_is_remembered_when_enabled(tmp_path, monkeypatch):
    a = base_voices(1)[0]
    c, _ = make_controller(tmp_path, monkeypatch, remember=True)
    c.transcript = Transcript(datetime(2026, 1, 1), "т")
    c.transcript.add(Utterance("Собеседник 1", 0, "р", 3, "system", "Собеседник 1"))
    c.transcript.centroids = {"Собеседник 1": a}
    c.last_file = tmp_path / "x.md"
    assert c.rename_speaker("Собеседник 1", "Аня") == 1
    assert VoiceBook(tmp_path / "voices.json").names == ["Аня"]  # голос ушёл в память
    assert c.known_voices() == ["Аня"]
    assert c.forget_voices() == 1 and c.known_voices() == []


def test_rename_without_memory_enabled_stores_nothing(tmp_path, monkeypatch):
    c, _ = make_controller(tmp_path, monkeypatch, remember=False)
    c.transcript = Transcript(datetime(2026, 1, 1), "т")
    c.transcript.add(Utterance("Собеседник 1", 0, "р", 3, "system", "Собеседник 1"))
    c.transcript.centroids = {"Собеседник 1": base_voices(1)[0]}
    c.last_file = tmp_path / "x.md"
    c.rename_speaker("Собеседник 1", "Аня")
    assert not (tmp_path / "voices.json").exists()


def test_refine_failure_is_reported_and_does_not_break_the_call(tmp_path, monkeypatch):
    from govorilka import controller as controller_module

    c, events = make_controller(tmp_path, monkeypatch)
    monkeypatch.setattr(controller_module, "refine_transcript", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("упало")))
    transcript = Transcript(datetime(2026, 1, 1), "т")
    diarizer = Diarizer(embed=lambda a: a)
    diarizer.history.append(Sample(0.0, base_voices(1)[0], 3.0, 1))
    saved = []
    c._refine(transcript, {"system": diarizer}, {"system": "Собеседник"}, lambda: saved.append(1))
    assert any("уточнить голоса" in e for e in events.errors) and saved == []


# ---------- настоящие голоса (нужны модели) ----------


def _models_present():
    from govorilka import models
    from govorilka.config import DEFAULT_MODEL

    return all(models.is_downloaded(k, n) for k, n in (("asr", DEFAULT_MODEL), ("vad", models.VAD_NAME), ("se", models.SPEAKER_REPO)))


needs_models = pytest.mark.skipif(not _models_present(), reason="модели не скачаны")


def _read(name):
    with wave.open(str(FIXTURES / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


@pytest.fixture(scope="module")
def engines():
    from govorilka.config import DEFAULT_MODEL
    from govorilka.engines import load_engines

    return load_engines(DEFAULT_MODEL, lambda _: None)


def _dialog_file(tmp_path):
    gap = np.zeros(SR, np.float32)
    parts = [_read("speech_ru.wav"), gap, _read("zira1.wav"), gap, _read("irina2.wav"), gap, _read("zira2.wav"), gap]
    path = tmp_path / "dialog.wav"
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.concatenate(parts) * 32767).astype(np.int16).tobytes())
    return path


def _run_file_job(path, engines, tmp_path):
    from govorilka.config import Settings
    from govorilka.filejob import FileJob

    done = {}
    job = FileJob(path, replace(Settings(), transcripts_dir=str(tmp_path)), engines, lambda e: None, lambda p: None,
                  lambda saved, error: done.update(saved=saved, error=error))
    job.start()
    job.wait()
    assert done["error"] is None
    return job


@needs_models
def test_real_voices_two_people_stay_two_people_after_refinement(engines, tmp_path):
    job = _run_file_job(_dialog_file(tmp_path), engines, tmp_path)
    refine_transcript(job.transcript, {"file": job.diarizer}, {"file": "Спикер"})
    labels = {u.speaker for u in job.transcript.utterances}
    assert labels == {"Спикер 1", "Спикер 2"}


@needs_models
def test_real_voices_refinement_repairs_a_mistake_made_during_recording(engines, tmp_path):
    """Если по ходу записи порог был слишком строгим и один человек получил несколько номеров, после созвона они сливаются."""
    from govorilka.speakers import Diarizer as RealDiarizer

    path = _dialog_file(tmp_path)
    job = _run_file_job(path, engines, tmp_path)
    strict = RealDiarizer(engines.speakers.embed, threshold=0.97)  # почти каждая фраза — «новый человек»
    for sample in job.diarizer.history:
        strict.history.append(replace(sample, label=0))
    transcript = Transcript(datetime(2026, 1, 1), "т")
    for i, u in enumerate(job.transcript.utterances):
        transcript.add(replace(u, speaker=f"Спикер {i + 1}", voice_id=f"Спикер {i + 1}"))  # онлайн: у каждой реплики свой номер
    assert len({u.speaker for u in transcript.utterances}) == len(job.transcript.utterances) > 2
    refine_transcript(transcript, {"file": strict}, {"file": "Спикер"})
    assert len({u.speaker for u in transcript.utterances}) == 2


@needs_models
def test_real_voices_known_person_is_recognized_in_the_next_call(engines, tmp_path):
    first = _run_file_job(_dialog_file(tmp_path), engines, tmp_path)
    refine_transcript(first.transcript, {"file": first.diarizer}, {"file": "Спикер"})
    irina_label = next(u.voice_id for u in first.transcript.utterances if "коллеги" in u.text.lower())
    book = VoiceBook(tmp_path / "voices.json")
    first.transcript.rename_voice(irina_label, "Ирина")
    assert remember_names(first.transcript, book) == 1

    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    second = _run_file_job(_dialog_file(tmp2), engines, tmp2)
    result = refine_transcript(second.transcript, {"file": second.diarizer}, {"file": "Спикер"}, VoiceBook(tmp_path / "voices.json"))
    assert result.recognized == ["Ирина"]
    assert any(u.speaker == "Ирина" and "коллеги" in u.text.lower() for u in second.transcript.utterances)
    assert any(u.speaker.startswith("Спикер") for u in second.transcript.utterances)  # вторая — незнакомая, осталась по номеру


# ---------- пересчёт голосов по ходу записи ----------


def test_stable_numbers_keep_the_number_people_already_saw():
    from govorilka.speakers import stable_numbers

    # пересчёт перенумеровал группы (0 и 1 поменялись местами), а люди видели «2» и «1»
    assert stable_numbers([0, 0, 1, 1, 2], [2, 2, 1, 1, 0]) == [2, 2, 1, 1, 3]
    # группы слились: номер достаётся тому, у кого больше фраз
    assert stable_numbers([0, 0, 0], [1, 1, 2]) == [1, 1, 1]
    # первые фразы: номера с 1 по порядку появления
    assert stable_numbers([1, 0, 1], [0, 0, 0]) == [1, 2, 1]


def test_live_regrouping_fixes_a_person_who_first_looked_new():
    a, b = base_voices(2)
    # первая фраза Ани вышла «странной» (похожа на неё лишь отчасти): по очереди она стала бы отдельным человеком
    vectors = iter([partly_like(a, 1.1), noisy(a), noisy(b), noisy(a), noisy(b), noisy(a)])
    diarizer = Diarizer(embed=lambda audio: next(vectors))
    long = np.zeros(SR * 3, np.float32)
    labels = [diarizer.assign(long, key=float(i)) for i in range(6)]
    final = {s.key: s.label for s in diarizer.history}
    assert len(set(final.values())) == 2  # в итоге двое, а не трое
    assert final[0.0] == final[1.0] == final[3.0] == final[5.0] != final[2.0] == final[4.0]
    assert labels[-1] == final[5.0]


def test_relabel_moves_lines_and_the_name_follows_the_person():
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(Utterance("Собеседник 2", 1.0, "привет", 2.0, "system", "Собеседник 2"))
    transcript.add(Utterance("Я", 1.5, "здравствуйте", 2.0, "mic", "Я"))
    transcript.add(Utterance("Собеседник 1", 3.0, "это я, Аня", 4.0, "system", "Собеседник 1"))
    transcript.rename_voice("Собеседник 2", "Аня")
    # пересчёт понял: фраза в 1.0 — тот же человек, что и в 3.0
    assert transcript.relabel("system", {1.0: "Собеседник 1"}) == 1
    speakers = [(u.speaker, u.voice_id) for u in transcript.utterances]
    assert speakers == [("Аня", "Собеседник 1"), ("Я", "Я"), ("Аня", "Собеседник 1")]
    assert transcript.aliases == {"Собеседник 1": "Аня"} and transcript.user_named == {"Собеседник 1"}


def test_phrase_processor_passes_regrouped_labels_to_the_transcript():
    from govorilka.pipeline import PhraseProcessor
    from govorilka.segmenter import SegmentEvent

    class Recognizer:
        def transcribe(self, audio, skip_if_busy=False):
            return "текст"

    class Regrouping:
        def assign(self, audio, key=None):
            return 1

        def drain_changes(self):
            return {0.0: 1, 2.0: 3}

    relabels, texts = [], []
    processor = PhraseProcessor(
        "system", "Собеседник", Recognizer(), texts.append, Regrouping(),
        on_relabel=lambda source, labels: relabels.append((source, labels)),
    )
    processor.handle(SegmentEvent("final", np.zeros(SR * 3, np.float32), 5.0))
    assert relabels == [("system", {0.0: "Собеседник 1", 2.0: "Собеседник 3"})]
    assert texts[0].speaker == "Собеседник 1"
