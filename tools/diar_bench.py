"""Стенд различения собеседников на синтетических отпечатках: python tools/diar_bench.py

Отпечатки похожи на настоящие wespeaker по статистике: один человек — близость фраз 0.4–0.85, разные люди — 0.0–0.4,
короткие фразы шумнее длинных. Люди говорят неравномерно: кто-то ведёт встречу, кто-то вставляет пару фраз.
Сравнивает число найденных собеседников и чистоту разметки (доля секунд речи, подписанных верно).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from govorilka.speakers import Diarizer, Sample, cluster_samples  # noqa: E402

DIM = 256
SHARED = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0   # насколько голоса похожи между собой
NOISE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.1    # насколько шумен отпечаток секундной фразы


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def make_call(speakers: int, rng: np.random.Generator) -> tuple[list[Sample], list[int]]:
    common = rng.normal(size=DIM)
    voices = _unit(SHARED * _unit(common) + _unit(rng.normal(size=(speakers, DIM))))  # люди немного похожи друг на друга
    share = rng.zipf(1.6, speakers).astype(float)  # ведущий говорит много, остальные — меньше
    share /= share.sum()
    samples, truth = [], []
    t = 0.0
    for _ in range(max(30, speakers * 14)):
        who = int(rng.choice(speakers, p=share))
        seconds = float(np.clip(rng.lognormal(1.0, 0.8), 0.7, 20.0))
        noise = NOISE / np.sqrt(max(seconds, 0.7))  # короткая фраза — шумный отпечаток

        samples.append(Sample(t, _unit(voices[who] + noise * _unit(rng.normal(size=DIM))), seconds, 0))
        truth.append(who)
        t += seconds + 0.5
    return samples, truth


def score(pred: list[int], truth: list[int], seconds: list[float]) -> float:
    """Доля веса (секунд или реплик), подписанного верно, при лучшем сопоставлении кластеров людям (жадно)."""
    pairs: dict[tuple[int, int], float] = {}
    for p, t, s in zip(pred, truth, seconds):
        pairs[(p, t)] = pairs.get((p, t), 0.0) + s
    used_p, used_t, good = set(), set(), 0.0
    for (p, t), s in sorted(pairs.items(), key=lambda kv: -kv[1]):
        if p in used_p or t in used_t:
            continue
        used_p.add(p)
        used_t.add(t)
        good += s
    return good / sum(seconds)


def online_labels(samples: list[Sample]) -> list[int]:
    """Как подписывает Diarizer по ходу записи: вместо звука подаём готовый отпечаток."""
    vectors = iter([s.vector for s in samples])
    diarizer = Diarizer(lambda _audio: next(vectors))
    for s in samples:
        diarizer.assign(np.zeros(int(s.seconds * 16000), np.float32), key=s.key)
    final = {x.key: x.label for x in diarizer.history + diarizer.short}
    return [final.get(s.key, diarizer.last) for s in samples]  # подписи на конец записи, с учётом переподписей


def similarity_stats(rng: np.random.Generator) -> None:
    samples, truth = make_call(6, rng)
    m = np.vstack([s.vector for s in samples])
    sims = m @ m.T
    t = np.asarray(truth)
    same = sims[(t[:, None] == t[None, :]) & ~np.eye(len(t), dtype=bool)]
    diff = sims[t[:, None] != t[None, :]]
    print(f"близость фраз: один человек {same.mean():.2f}±{same.std():.2f}, разные {diff.mean():.2f}±{diff.std():.2f}")


def main() -> None:
    rng = np.random.default_rng(7)
    similarity_stats(rng)
    print("людей  говорило  найдено  точно угадано число  верно подписано реплик   по ходу записи: найдено, верно")
    for speakers in (2, 3, 4, 6, 8, 10):
        found, spoke, exact, acc, live_found, live_acc = [], [], 0, [], [], []
        for _ in range(20):
            samples, truth = make_call(speakers, rng)
            real = len(set(truth))
            pred = cluster_samples([s.vector for s in samples], [s.seconds for s in samples])
            found.append(len(set(pred)))
            spoke.append(real)
            exact += len(set(pred)) == real
            acc.append(score(pred, truth, [1.0] * len(samples)))  # каждая реплика весит одинаково: так ошибки и видны в ленте
            live = online_labels(samples)
            live_found.append(len(set(live)))
            live_acc.append(score(live, truth, [1.0] * len(samples)))
        print(f"{speakers:5d}  {np.mean(spoke):8.1f}  {np.mean(found):7.1f}  {exact:17d}/20  {np.mean(acc):21.1%}"
              f"   {np.mean(live_found):6.1f}  {np.mean(live_acc):6.1%}")


if __name__ == "__main__":
    main()
