"""Различение собеседников по голосу.

Для каждой фразы считаем «отпечаток» голоса (wespeaker, 256 чисел) и сравниваем
с уже известными голосами. Похож — тот же человек, нет — новый собеседник.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import count

import numpy as np

from govorilka import models
from govorilka.config import SAMPLE_RATE

SAME_SPEAKER = 0.5  # косинусная близость: свой голос 0.6–0.9, чужой 0.3–0.45
MIN_EMBED_S = 1.0  # фраза короче — отпечаток ненадёжен: в кластеры голосов не входит
MIN_SHORT_S = 0.6  # «да», «угу» короче этого вовсе не сравниваем: отпечаток из шума
SHORT_SAME = 0.50  # короткая фраза относится к голосу, если похожа на него хотя бы так (иначе — тот же, что был)
MAX_SPEAKERS = 12


class SpeakerModel:
    """Модель отпечатков голоса (26 МБ). Одна на приложение."""

    def __init__(self, on_status: models.StatusCallback = lambda _: None) -> None:
        from onnx_asr.loader import Manager

        models.ensure_downloaded("se", models.SPEAKER_REPO, "модель голосов", on_status)
        self._model = Manager(providers=models.CPU).create_se(
            models.SPEAKER_REPO, models.local_dir(models.SPEAKER_REPO)
        )
        self._lock = threading.Lock()

    def embed(self, audio: np.ndarray) -> np.ndarray:
        with self._lock:
            vector = np.asarray(self._model.embedding(audio, sample_rate=SAMPLE_RATE), dtype=np.float32).reshape(-1)
        return vector / (np.linalg.norm(vector) or 1.0)


@dataclass
class _Voice:
    total: np.ndarray
    count: int = 1

    @property
    def centroid(self) -> np.ndarray:
        return self.total / (np.linalg.norm(self.total) or 1.0)


@dataclass(frozen=True)
class Sample:
    """Отпечаток одной фразы: по key (время начала фразы) находим реплику в транскрипте."""

    key: float
    vector: np.ndarray
    seconds: float
    label: int  # номер, выданный по ходу записи


@dataclass
class Diarizer:
    """Подписывает голоса по ходу записи: 1, 2, 3… и копит отпечатки для уточнения после созвона.

    Каждую новую фразу не сравнивают «по очереди» с уже известными голосами (так первые фразы, на которых голос ещё
    не выучен, плодят лишних собеседников), а заново группируют все фразы сразу, как после созвона. Номера при этом
    держатся стабильными: группа получает номер, который больше всего её фраз уже носили. Если пересчёт поменял
    подпись у прошлых фраз, это видно в drain_changes() — транскрипт их переподпишет.
    """

    embed: Callable[[np.ndarray], np.ndarray]
    threshold: float = SAME_SPEAKER
    voices: list[_Voice] = field(default_factory=list)  # для фраз без ключа (без истории): прежнее сравнение по очереди
    last: int = 1
    history: list[Sample] = field(default_factory=list)
    short: list[Sample] = field(default_factory=list)  # короткие фразы: подписаны по ближайшему голосу, в группы не входят
    _centroids: dict[int, np.ndarray] = field(default_factory=dict)
    _changes: dict[float, int] = field(default_factory=dict)

    def assign(self, audio: np.ndarray, key: float | None = None) -> int:
        seconds = len(audio) / SAMPLE_RATE
        if seconds < MIN_SHORT_S or (key is None and seconds < MIN_EMBED_S):
            return self.last  # короткое «да», «угу» — скорее всего, тот же человек
        vector = self.embed(audio)
        if key is None:
            self.last = self._label_for(vector)
            return self.last
        if seconds < MIN_EMBED_S:
            label = self._nearest(vector) or self.last
            self.short.append(Sample(key, vector, seconds, label))
        else:
            self.history.append(Sample(key, vector, seconds, 0))
            self._regroup()
            label = self.history[-1].label
        self.last = label
        return label

    def drain_changes(self) -> dict[float, int]:
        """Прошлые фразы, чья подпись поменялась после пересчёта: ключ фразы -> новый номер."""
        changes, self._changes = self._changes, {}
        return changes

    def _regroup(self) -> None:
        vectors = [s.vector for s in self.history]
        clusters = cluster_samples(vectors, [s.seconds for s in self.history])
        numbers = stable_numbers(clusters, [s.label for s in self.history])
        regrouped = []
        for sample, number in zip(self.history, numbers):
            if sample.label and sample.label != number:
                self._changes[sample.key] = number
            regrouped.append(replace(sample, label=number) if sample.label != number else sample)
        self.history = regrouped
        self._centroids = cluster_centroids(vectors, [s.seconds for s in regrouped], numbers)
        shorts = []
        for sample in self.short:  # короткие фразы идут за своим голосом, если он переехал
            number = self._nearest(sample.vector) or sample.label
            if number != sample.label:
                self._changes[sample.key] = number
                sample = replace(sample, label=number)
            shorts.append(sample)
        self.short = shorts

    def _nearest(self, vector: np.ndarray) -> int | None:
        if not self._centroids:
            return None
        scores = {number: float(vector @ centroid) for number, centroid in self._centroids.items()}
        best = max(scores, key=scores.__getitem__)
        return best if scores[best] >= SHORT_SAME else None

    def _label_for(self, vector: np.ndarray) -> int:
        if self.voices:
            scores = [float(vector @ v.centroid) for v in self.voices]
            best = int(np.argmax(scores))
            if scores[best] >= self.threshold or len(self.voices) >= MAX_SPEAKERS:
                voice = self.voices[best]
                self.voices[best] = _Voice(voice.total + vector, voice.count + 1)
                return best + 1
        self.voices.append(_Voice(vector))
        return len(self.voices)


def stable_numbers(clusters: list[int], previous: list[int]) -> list[int]:
    """Номера групп для показа. Группа получает номер, который носило больше всего её фраз: «Собеседник 2» остаётся
    «Собеседником 2», даже если пересчёт перетасовал группы. Новой группе — наименьший свободный номер с 1."""
    overlap = Counter((c, p) for c, p in zip(clusters, previous) if p >= 1)
    numbers: dict[int, int] = {}
    used: set[int] = set()
    for (cluster, number), _count in sorted(overlap.items(), key=lambda kv: (-kv[1], kv[0][1], kv[0][0])):
        if cluster in numbers or number in used:
            continue
        numbers[cluster] = number
        used.add(number)
    for cluster in dict.fromkeys(clusters):  # по порядку первого появления
        if cluster not in numbers:
            free = next(n for n in count(1) if n not in used)
            numbers[cluster] = free
            used.add(free)
    return [numbers[c] for c in clusters]


# ---------- уточнение после созвона ----------

OFFLINE_SAME = 0.50  # близость голосов при группировке всех фраз сразу (подобрано на стенде tools/diar_bench.py)
TINY_SECONDS = 3.0  # группа короче — скорее всего, осколок чужого голоса, а не новый человек
TINY_MERGE = 0.47  # осколок сливаем с ближайшим голосом, если он заметно похож (разные люди дают около 0.43)


def cluster_samples(vectors: list[np.ndarray], seconds: list[float], threshold: float = OFFLINE_SAME) -> list[int]:
    """Номера кластеров 0, 1, 2… по порядку первого появления для каждого отпечатка.

    Иерархическая кластеризация со средней связью по косинусному расстоянию: смотрит на все фразы сразу, а не
    по очереди, поэтому первые фразы, на которых голос ещё не «выучен», попадают в правильную группу.
    """
    count = len(vectors)
    if count == 0:
        return []
    if count == 1:
        return [0]
    from scipy.cluster.hierarchy import fcluster, linkage

    matrix = np.vstack(vectors).astype(np.float64)
    tree = linkage(matrix, method="average", metric="cosine")
    labels = fcluster(tree, t=1.0 - threshold, criterion="distance") - 1
    labels = _merge_small(matrix, np.asarray(seconds, dtype=np.float64), labels)
    return _renumber(labels.tolist())


def _centroids(matrix: np.ndarray, seconds: np.ndarray, labels: np.ndarray) -> dict[int, np.ndarray]:
    result = {}
    for label in sorted(set(labels.tolist())):
        members = labels == label
        total = (matrix[members] * seconds[members, None]).sum(axis=0)  # длинные фразы весят больше коротких
        result[label] = total / (np.linalg.norm(total) or 1.0)
    return result


def _merge_small(matrix: np.ndarray, seconds: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Сливает мелкие кластеры (осколки) с ближайшим голосом; если кластеров больше MAX_SPEAKERS — самые мелкие в первую очередь."""
    labels = labels.copy()
    while True:
        centroids = _centroids(matrix, seconds, labels)
        if len(centroids) < 2:
            return labels
        totals = {label: float(seconds[labels == label].sum()) for label in centroids}
        overflow = len(centroids) > MAX_SPEAKERS
        merged = False
        for label in sorted(totals, key=totals.__getitem__):  # от самых коротких
            if totals[label] >= TINY_SECONDS and not overflow:
                return labels  # остальные ещё длиннее: это настоящие собеседники
            similarity = {other: float(centroids[label] @ vec) for other, vec in centroids.items() if other != label}
            nearest = max(similarity, key=similarity.__getitem__)
            if overflow or similarity[nearest] >= TINY_MERGE:
                labels[labels == label] = nearest
                merged = True
                break
        if not merged:
            return labels


def _renumber(labels: list[int]) -> list[int]:
    order: dict[int, int] = {}
    for label in labels:
        order.setdefault(label, len(order))
    return [order[label] for label in labels]


def cluster_centroids(vectors: list[np.ndarray], seconds: list[float], labels: list[int]) -> dict[int, np.ndarray]:
    """Усреднённый голос каждого кластера: нужен, чтобы узнавать знакомых и запоминать новых."""
    if not vectors:
        return {}
    return _centroids(np.vstack(vectors).astype(np.float64), np.asarray(seconds, dtype=np.float64), np.asarray(labels))
