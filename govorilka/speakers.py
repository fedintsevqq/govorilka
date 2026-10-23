"""Различение собеседников по голосу.

Для каждой фразы считаем «отпечаток» голоса (wespeaker, 256 чисел) и сравниваем
с уже известными голосами. Похож — тот же человек, нет — новый собеседник.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from govorilka import models
from govorilka.config import SAMPLE_RATE

SAME_SPEAKER = 0.5  # косинусная близость: свой голос 0.6–0.9, чужой 0.3–0.45
MIN_EMBED_S = 1.0  # по более короткой фразе отпечаток ненадёжен
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
    """Раздаёт номера голосам по мере их появления: 1, 2, 3…, а заодно копит отпечатки для уточнения после созвона."""

    embed: Callable[[np.ndarray], np.ndarray]
    threshold: float = SAME_SPEAKER
    voices: list[_Voice] = field(default_factory=list)
    last: int = 1
    history: list[Sample] = field(default_factory=list)

    def assign(self, audio: np.ndarray, key: float | None = None) -> int:
        if len(audio) < MIN_EMBED_S * SAMPLE_RATE:
            return self.last  # короткое «да», «угу» — скорее всего, тот же человек
        vector = self.embed(audio)
        label = self._label_for(vector)
        if key is not None:
            self.history.append(Sample(key, vector, len(audio) / SAMPLE_RATE, label))
        return label

    def _label_for(self, vector: np.ndarray) -> int:
        if self.voices:
            scores = [float(vector @ v.centroid) for v in self.voices]
            best = int(np.argmax(scores))
            if scores[best] >= self.threshold or len(self.voices) >= MAX_SPEAKERS:
                voice = self.voices[best]
                self.voices[best] = _Voice(voice.total + vector, voice.count + 1)
                self.last = best + 1
                return self.last
        self.voices.append(_Voice(vector))
        self.last = len(self.voices)
        return self.last


# ---------- уточнение после созвона ----------

OFFLINE_SAME = 0.48  # близость голосов при кластеризации по всем фразам сразу (по ходу записи — 0.5)
TINY_SECONDS = 3.0  # кластер короче — скорее всего, осколок чужого голоса, а не новый человек
TINY_MERGE = 0.36  # осколок сливаем с ближайшим голосом, если они хотя бы так похожи


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
