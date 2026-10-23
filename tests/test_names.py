from datetime import datetime

from govorilka.names import known_name, suggest_names
from govorilka.transcript import Transcript, Utterance


def line(voice, start, text, source="system"):
    return Utterance(voice, start, text, start + 2, source, voice)


def test_self_introduction_names_the_speaker():
    items = [line("Собеседник 1", 0, "Всем привет, меня зовут Аня, я тимлид."), line("Собеседник 2", 3, "Это Олег, на связи.")]
    assert suggest_names(items, {"system"}, {}) == {"Собеседник 1": "Аня", "Собеседник 2": "Олег"}


def test_address_names_the_one_who_answers_only_when_confirmed():
    once = [line("Я", 0, "Ира, что скажешь?", "mic"), line("Собеседник 3", 3, "Думаю, успеем.")]
    assert suggest_names(once, {"system"}, {}) == {}  # одно обращение — ещё догадка
    twice = once + [line("Я", 10, "Ира, а по срокам?", "mic"), line("Собеседник 3", 13, "До пятницы.")]
    assert suggest_names(twice, {"system"}, {}) == {"Собеседник 3": "Ира"}


def test_thanks_names_the_previous_speaker():
    items = [
        line("Собеседник 2", 0, "Я посмотрю логи."),
        line("Собеседник 1", 3, "Спасибо, Дима."),
        line("Собеседник 2", 6, "Сделаю отчёт."),
        line("Собеседник 1", 9, "Хорошо, Дима!"),
    ]
    assert suggest_names(items, {"system"}, {}) == {"Собеседник 2": "Дима"}


def test_ordinary_words_are_not_names():
    items = [line("Собеседник 1", 0, "Это плохо, меня зовут на другой созвон."), line("Собеседник 1", 4, "Я рад, это важно.")]
    assert suggest_names(items, {"system"}, {}) == {}
    assert known_name("Алёна") == "Алёна" and known_name("алена") == "Алёна" and known_name("стол") is None


def test_user_names_and_taken_names_are_kept():
    items = [line("Собеседник 1", 0, "Меня зовут Аня."), line("Собеседник 2", 3, "А меня зовут Аня тоже.")]
    assert suggest_names(items, {"system"}, {"Собеседник 1": "Анна Петровна"}) == {"Собеседник 2": "Аня"}
    assert suggest_names(items, {"system"}, {"Собеседник 3": "Аня"}) == {}


def test_names_from_speech_are_not_treated_as_given_by_the_user():
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(line("Собеседник 1", 0, "Меня зовут Аня."))
    assert transcript.name_voices({"Собеседник 1": "Аня"}) == ["Аня"]
    assert transcript.utterances[0].speaker == "Аня" and transcript.user_named == set()
    transcript.rename_voice("Собеседник 1", "Анна")
    assert transcript.name_voices({"Собеседник 1": "Аня"}) == []  # пользователь поправил — догадка его не перебьёт
