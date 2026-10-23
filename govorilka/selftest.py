"""Проверка собранного приложения без окна: `Govorilka --selftest файл`.

Расшифровывает файл тем же путём, что и кнопка «Открыть файл…»: PyAV,
Silero VAD, GigaAM, различение голосов. Пишет результат в selftest.txt
в папке данных. Код выхода 0 — всё работает.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from dataclasses import replace
from pathlib import Path

from govorilka.config import AUDIOTAP_PATH, DATA_DIR, DEFAULT_MODEL, Settings
from govorilka.engines import load_engines
from govorilka.filejob import FileJob

log = logging.getLogger(__name__)


def _check_devices() -> str:
    from govorilka.audio import AudioError, AudioSystem

    audio = AudioSystem()
    try:
        lines = []
        for label, find in (("микрофон", audio.default_mic), ("системный звук", audio.default_loopback)):
            try:
                lines.append(f"{label}: {find().name}")
            except AudioError as exc:  # на сервере сборки устройств может не быть — это не провал
                lines.append(f"{label}: нет ({exc})")
        return "\n".join(lines)
    finally:
        audio.close()


def _echo_cancellation_problem() -> str | None:
    """Эхоподавление в собранном приложении: нативная библиотека на месте и реально гасит эхо."""
    import numpy as np

    try:
        from govorilka.aec import EchoCanceller

        canceller = EchoCanceller()
    except Exception as exc:  # noqa: BLE001
        return f"эхоподавление не загрузилось: {exc}"
    rng = np.random.default_rng(0)
    speaker = (rng.normal(0, 0.1, 16_000 * 6)).astype(np.float32)  # «звук колонок»
    echo = np.concatenate([np.zeros(480, np.float32), speaker[:-480]]) * 0.4  # он же в микрофоне через 30 мс
    cleaned = []
    for i in range(0, len(echo), 1600):
        canceller.add_reference(speaker[i : i + 1600], i / 16_000)
        cleaned.append(canceller.process(echo[i : i + 1600], i / 16_000)[0])
    tail = slice(16_000 * 3, None)  # первые секунды модуль подстраивается
    before = float(np.mean(echo[tail] ** 2))
    after = float(np.mean(np.concatenate(cleaned)[tail] ** 2)) + 1e-12
    suppression = 10 * np.log10(before / after)
    return None if suppression > 10 else f"эхоподавление работает слабо: {suppression:.1f} дБ"


def _problems(text: str) -> list[str]:
    """Что не так со сборкой. Пустой список — всё хорошо.

    «Непустой текст» — слабая проверка: испорченная модель тоже может что-то выдать. Поэтому CI передаёт
    слово, которое обязано прозвучать в тестовой записи (GOVORILKA_SELFTEST_EXPECT), а на Mac проверяем,
    что помощник записи звука лежит внутри приложения и запускается.
    """
    problems = []
    expected = os.environ.get("GOVORILKA_SELFTEST_EXPECT", "").strip().lower()
    if expected and expected not in text.lower():
        problems.append(f"в расшифровке нет ожидаемого слова «{expected}»")
    echo_problem = _echo_cancellation_problem()
    if echo_problem:
        problems.append(echo_problem)
    if sys.platform == "darwin" and not (AUDIOTAP_PATH.is_file() and os.access(AUDIOTAP_PATH, os.X_OK)):
        problems.append(f"помощник записи звука не найден или не запускается: {AUDIOTAP_PATH}")
    return problems


def run(media: Path) -> int:
    report = DATA_DIR / "selftest.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        engines = load_engines(DEFAULT_MODEL, lambda s: log.info("%s", s))
        result: dict[str, object] = {}
        with tempfile.TemporaryDirectory() as out:
            settings = replace(Settings(), transcripts_dir=out)
            job = FileJob(
                media,
                settings,
                engines,
                on_text=lambda _: None,
                on_progress=lambda _: None,
                on_done=lambda saved, error: result.update(saved=saved, error=error),
            )
            job.start()
            job.wait()
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        lines = [f"[{u.speaker}] {u.text}" for u in job.transcript.utterances]
        problems = _problems(" ".join(lines))
        status = "FAIL" if problems or not lines else "OK"
        report.write_text("\n".join([status, *problems, *lines, _check_devices()]) + "\n", encoding="utf-8")
        return 0 if status == "OK" else 1
    except Exception:
        log.exception("Самопроверка не прошла")
        report.write_text("FAIL\n" + traceback.format_exc(), encoding="utf-8")
        return 1
