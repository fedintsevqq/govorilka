from govorilka.echo import is_echo
from govorilka.transcript import Utterance

OTHER = [
    Utterance("Собеседник", 3.5, "Сегодня обсуждаем регрессионное тестирование релиза.", 7.0),
    Utterance("Собеседник", 8.2, "Нужно проверить оплату картой и вход по номеру телефона.", 12.0),
]


def test_mic_phrase_spanning_two_others_is_echo():
    mic = Utterance("Я", 3.7, "Сегодня обсуждаем регрессионное тестирование релиза. Нужно проверить оплату картой", 11.0)
    assert is_echo(mic, OTHER)


def test_own_speech_is_not_echo():
    mic = Utterance("Я", 4.0, "Я возьму смоук на себя и отпишусь в чат", 6.0)
    assert not is_echo(mic, OTHER)


def test_same_words_far_in_time_is_not_echo():
    mic = Utterance("Я", 60.0, "Нужно проверить оплату картой", 62.0)
    assert not is_echo(mic, OTHER)


def test_unknown_end_falls_back_to_start():
    u = Utterance("Я", 5.0, "текст")
    assert u.end == 5.0
