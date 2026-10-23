"""Имена собеседников из самого разговора.

Люди сами называют себя и друг друга: «меня зовут Аня», «это Олег, на связи», «Ира, что скажешь?», «спасибо, Дима».
По таким фразам Говорилка подписывает голоса сама — с десятком участников не нужно вводить каждого вручную.

Осторожно, чтобы не выдумать: имя берётся только из списка настоящих имён (иначе «это плохо» дало бы «Плохо»),
представление себя считается сразу, обращение — только если повторилось или подтверждено ответом. Имена, которые
дал пользователь, не трогаем, одно имя — одному человеку.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from govorilka.transcript import Utterance

ANSWER_WITHIN_S = 8.0  # на обращение «Аня, …» отвечают быстро: следующий другой голос в этом окне — Аня
SELF_INTRO = 3  # «меня зовут Аня» — почти наверняка
ADDRESS = 1  # «Аня, …» — догадка, нужна вторая такая же
ENOUGH = 2

# Полные имена и самые частые короткие формы. Ключ — как пишут в речи (нижний регистр, «е» вместо «ё»), значение —
# как подписать. Короткую форму оставляем короткой: в созвоне человека так и зовут.
_RAW = """
александр саша алексей леша андрей анатолий антон аркадий арсений артем артур богдан борис вадим валентин валерий
василий вася виктор витя виталий влад владимир вова володя владислав всеволод вячеслав слава гена геннадий георгий
гоша глеб григорий гриша давид даниил данил даня денис дима дмитрий евгений женя егор захар иван ваня игорь илья
кирилл константин костя лев лева леонид максим макс марат марк матвей миша михаил никита николай коля олег павел
паша петр петя роман рома руслан семен сергей сережа серега станислав стас степан тимофей тимур федор федя филипп
эдуард юрий юра ярослав
александра алена алина алиса алла анастасия настя ангелина анна аня антонина арина валентина валя валерия лера
вера вероника вика виктория галина галя дарья даша диана ева евгения екатерина катя елена лена елизавета лиза жанна
зоя инна ирина ира карина кристина ксения ксюша лариса лида лидия любовь люба людмила люда маргарита рита марина
мария маша милана надежда надя наталья наталия наташа нина оксана ольга оля полина раиса регина светлана света
софия софья соня таисия тамара татьяна таня ульяна юлия юля яна
"""
_YO = {"алена": "Алёна", "петр": "Пётр", "федор": "Фёдор", "семен": "Семён", "артем": "Артём", "лева": "Лёва",
       "леша": "Лёша", "сережа": "Серёжа", "серега": "Серёга", "петя": "Петя"}  # как пишут правильно, с «ё»
NAMES = {word: _YO.get(word, word.capitalize()) for word in _RAW.split()}

_WORD = r"([А-Яа-яЁё]+)"
_INTRO = [
    re.compile(rf"\bменя зовут {_WORD}", re.IGNORECASE),
    re.compile(rf"^(?:да,?\s+|привет,?\s+|здравствуйте,?\s+|добрый день,?\s+)?это {_WORD}\b", re.IGNORECASE),
    re.compile(rf"\b{_WORD},? на связи\b", re.IGNORECASE),
    re.compile(rf"\bс вами {_WORD}\b", re.IGNORECASE),
    re.compile(rf"^(?:привет,?\s+|здравствуйте,?\s+)?я {_WORD}[,.!]", re.IGNORECASE),
]
_ADDRESS_START = re.compile(rf"^{_WORD},", re.IGNORECASE)  # «Аня, что скажешь?» — ответит Аня
_THANKS = re.compile(rf"\b(?:спасибо|благодарю|понял|поняла|ок|хорошо),? {_WORD}[.!]?$", re.IGNORECASE)  # «спасибо, Дима» — Дима говорил


def known_name(word: str) -> str | None:
    return NAMES.get(word.lower().replace("ё", "е"))


def suggest_names(utterances: Iterable[Utterance], sources: set[str], taken: dict[str, str]) -> dict[str, str]:
    """Ярлык голоса -> имя, которое прозвучало в разговоре. taken — уже подписанные (их не трогаем, их имена не раздаём)."""
    items = [u for u in utterances if u.text]
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i, u in enumerate(items):
        voice = u.voice_id or u.speaker
        if u.source in sources:
            for pattern in _INTRO:
                match = pattern.search(u.text)
                name = known_name(match.group(1)) if match else None
                if name:
                    votes[voice][name] += SELF_INTRO
                    break
            thanks = _THANKS.search(u.text)
            name = known_name(thanks.group(1)) if thanks else None
            previous = _previous_other(items, i)
            if name and previous is not None:
                votes[previous][name] += ADDRESS
        address = _ADDRESS_START.match(u.text)
        name = known_name(address.group(1)) if address else None
        answer = _next_other(items, i, sources) if name else None
        if name and answer is not None:
            votes[answer][name] += ADDRESS
    return _pick(votes, taken)


def _previous_other(items: list[Utterance], i: int) -> str | None:
    voice = items[i].voice_id or items[i].speaker
    for u in reversed(items[:i]):
        other = u.voice_id or u.speaker
        if other != voice and items[i].start - u.end <= ANSWER_WITHIN_S:
            return other
        if items[i].start - u.end > ANSWER_WITHIN_S:
            return None
    return None


def _next_other(items: list[Utterance], i: int, sources: set[str]) -> str | None:
    voice = items[i].voice_id or items[i].speaker
    for u in items[i + 1 :]:
        if u.start - items[i].end > ANSWER_WITHIN_S:
            return None
        other = u.voice_id or u.speaker
        if other != voice:
            return other if u.source in sources else None
    return None


def _pick(votes: dict[str, dict[str, int]], taken: dict[str, str]) -> dict[str, str]:
    """Самые уверенные пары первыми; одно имя — одному голосу. Чужие имена и уже подписанных не трогаем."""
    used = set(taken.values())
    pairs = sorted(
        ((score, voice, name) for voice, names in votes.items() for name, score in names.items() if score >= ENOUGH),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    result: dict[str, str] = {}
    for _score, voice, name in pairs:
        if voice in taken or voice in result or name in used:
            continue
        result[voice] = name
        used.add(name)
    return result
