"""Проверка собранного приложения без окна: `Govorilka --selftest файл`.

Расшифровывает файл тем же путём, что и кнопка «Открыть файл…»: PyAV,
Silero VAD, GigaAM, различение голосов. Пишет результат в selftest.txt
в папке данных. Код выхода 0 — всё работает.
"""

from __future__ import annotations

import logging
import tempfile
import traceback
from dataclasses import replace
from pathlib import Path

from govorilka.config import DATA_DIR, DEFAULT_MODEL, Settings
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
        report.write_text("OK\n" + "\n".join(lines) + "\n" + _check_devices() + "\n", encoding="utf-8")
        return 0 if lines else 1
    except Exception:
        log.exception("Самопроверка не прошла")
        report.write_text("FAIL\n" + traceback.format_exc(), encoding="utf-8")
        return 1
