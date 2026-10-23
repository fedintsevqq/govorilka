"""Уточнение подписей говорящих после созвона.

По ходу записи голоса различаются «по очереди»: первые фразы голос ещё не знаком, и один человек может получить два номера.
После остановки все отпечатки кластеризуются заново сразу, лишние номера сливаются, нумерация идёт по порядку появления.
Имена, которые пользователь уже дал, переезжают на новые номера; знакомые голоса из памяти называются сами.

Результат собирается целиком и применяется к транскрипту одной атомарной операцией (`Transcript.apply_refined`):
параллельное переименование не может «потеряться» между чтением и записью.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace

import numpy as np

from govorilka.names import suggest_names
from govorilka.speakers import Diarizer, Sample, cluster_centroids, cluster_samples
from govorilka.transcript import Transcript, Utterance
from govorilka.voices import VoiceBook

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefineResult:
    speakers: int  # сколько собеседников нашлось
    changed: int  # у скольких реплик подпись поменялась
    recognized: list[str]  # кого узнали по памяти голосов
    heard: list[str] = ()  # кого назвали по самому разговору («меня зовут Аня»)


def _usable(sample: Sample) -> bool:
    """Пустой, нулевой или испорченный отпечаток (например, из тишины) нельзя кластеризовать: scipy на нём падает."""
    vector = sample.vector
    return bool(np.isfinite(vector).all() and np.linalg.norm(vector) > 0)


def refine_transcript(
    transcript: Transcript,
    diarizers: dict[str, Diarizer],
    base_names: dict[str, str],
    voicebook: VoiceBook | None = None,
) -> RefineResult:
    """Переподписывает реплики собеседников. diarizers и base_names — по источникам звука («system», «file»)."""
    items, aliases, user_named = transcript.snapshot()
    label_for: dict[tuple[str, float], str] = {}
    centroids: dict[str, np.ndarray] = {}
    for source, diarizer in diarizers.items():
        samples = [s for s in diarizer.history if _usable(s)]
        if not samples:
            continue
        vectors, seconds = [s.vector for s in samples], [s.seconds for s in samples]
        clusters = cluster_samples(vectors, seconds)
        means = cluster_centroids(vectors, seconds, clusters)
        base = base_names.get(source, "Собеседник")
        for sample, cluster in zip(samples, clusters):
            label = f"{base} {cluster + 1}"
            label_for[(source, sample.key)] = label
            centroids[label] = means[cluster]
    if not label_for:
        return RefineResult(0, 0, [])

    # Реплики без отпечатка (короткие «да», «угу») наследуют подпись предыдущей реплики того же источника.
    first_label: dict[str, str] = {}
    for (source, _key), label in sorted(label_for.items(), key=lambda kv: kv[0][1]):
        first_label.setdefault(source, label)
    relabeled: list[Utterance] = []
    last_label: dict[str, str] = {}
    members: dict[str, list[str]] = {}  # новый ярлык -> прежние ярлыки его реплик (чтобы перенести имена)
    for u in items:
        if u.source not in diarizers:  # микрофон, отметки, служебные строки — без изменений
            relabeled.append(u)
            continue
        label = label_for.get((u.source, u.start)) or last_label.get(u.source) or first_label.get(u.source)
        if label is None:
            relabeled.append(u)
            continue
        last_label[u.source] = label
        members.setdefault(label, []).append(u.voice_id or u.speaker)
        relabeled.append(replace(u, speaker=label, voice_id=label))

    names, named_by_user = _carry_names(aliases, user_named, members)
    recognized = _recognize(centroids, voicebook, names)
    heard = suggest_names(relabeled, set(diarizers), names)  # безымянных называем по разговору
    names |= heard
    final = [replace(u, speaker=names[u.voice_id]) if u.voice_id in names else u for u in relabeled]
    changed = sum(1 for a, b in zip(items, final) if (a.speaker, a.voice_id) != (b.speaker, b.voice_id))

    # Подписи прежних ярлыков заменены новыми; остальные имена (например, переименованное «Я») остаются.
    replaced_labels = {old for olds in members.values() for old in olds}
    merged_aliases = {k: v for k, v in aliases.items() if k not in replaced_labels} | names
    merged_user = {k for k in user_named if k not in replaced_labels} | named_by_user
    transcript.apply_refined(final, merged_aliases, centroids, merged_user)
    return RefineResult(
        len(centroids), changed, [names[label] for label in names if label in recognized], list(heard.values())
    )


def _carry_names(
    aliases: dict[str, str], user_named: set[str], members: dict[str, list[str]]
) -> tuple[dict[str, str], set[str]]:
    """Имена переезжают на новый кластер, где больше всего реплик прежнего ярлыка. Назначение идёт по убыванию числа
    реплик, а не по порядку кластеров: иначе первый кластер мог бы забрать чужое имя."""
    pairs = [(count, label, old) for label, olds in members.items() for old, count in Counter(olds).items() if old in aliases]
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    names: dict[str, str] = {}
    by_user: set[str] = set()
    used_labels: set[str] = set()
    used_olds: set[str] = set()
    for _count, label, old in pairs:
        if label in used_labels or old in used_olds:
            continue
        used_labels.add(label)
        used_olds.add(old)
        names[label] = aliases[old]
        if old in user_named:
            by_user.add(label)
    return names, by_user


def _recognize(centroids: dict[str, np.ndarray], voicebook: VoiceBook | None, names: dict[str, str]) -> set[str]:
    """Называет безымянных по памяти голосов. Пары «кластер — знакомый» берутся по убыванию сходства: самый уверенный
    выбор первым, один человек — одно имя, одно имя — один человек."""
    if voicebook is None:
        return set()
    taken_names = set(names.values())
    candidates = [
        (score, label, name)
        for label, vector in centroids.items()
        if label not in names
        for name, score in voicebook.rank(vector)
    ]
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    recognized: set[str] = set()
    for _score, label, name in candidates:
        if label in names or name in taken_names:
            continue
        names[label] = name
        taken_names.add(name)
        recognized.add(label)
    return recognized


def remember_names(transcript: Transcript, voicebook: VoiceBook) -> int:
    """Запоминает в памяти голосов тех, кого пользователь назвал сам. Угаданных по памяти не трогаем: ошибка
    узнавания иначе закреплялась бы и дрейфовала от созвона к созвону."""
    _items, aliases, user_named = transcript.snapshot()
    centroids = transcript.centroids_snapshot()
    saved = 0
    for label in sorted(user_named):
        name, vector = aliases.get(label), centroids.get(label)
        if vector is not None and name and name != label and voicebook.remember(name, vector):
            saved += 1
    if saved:
        voicebook.save()
    return saved
