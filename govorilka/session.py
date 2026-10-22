"""Одна запись созвона: устройства, потоки распознавания, транскрипт и автосохранение."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from govorilka.audio import AudioError, AudioSystem, Capture
from govorilka.config import SPEAKER_ME, SPEAKER_OTHERS, Settings
from govorilka.echo import is_echo
from govorilka.pipeline import LevelCallback, SourcePipeline, TextEvent
from govorilka.recognizer import Recognizer
from govorilka.transcript import AutoSaver, Transcript, Utterance

log = logging.getLogger(__name__)


class Session:
    def __init__(
        self,
        settings: Settings,
        recognizer: Recognizer,
        title: str,
        on_text: Callable[[TextEvent], None],
        on_level: LevelCallback,
    ) -> None:
        if not (settings.capture_mic or settings.capture_system):
            raise AudioError("Включите хотя бы один источник: микрофон или звук компьютера")
        self.transcript = Transcript(datetime.now(), title)
        self.saver = AutoSaver(self.transcript, settings.transcripts_path)
        self._settings = settings
        self._recognizer = recognizer
        self._on_text = on_text
        self._on_level = on_level
        self._audio = AudioSystem()
        self._captures: list[Capture] = []
        self._pipelines: list[SourcePipeline] = []
        self.device_names: list[str] = []
        self._closed = False
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.saver.path

    def start(self) -> None:
        t0 = time.monotonic()
        sources = []
        if self._settings.capture_mic:
            sources.append((SPEAKER_ME, self._audio.default_mic))
        if self._settings.capture_system:
            sources.append((SPEAKER_OTHERS, self._audio.default_loopback))
        try:
            for speaker, find_device in sources:
                device = find_device()
                pipeline = SourcePipeline(speaker, self._recognizer, t0, self._handle_text, self._on_level)
                pipeline.start()
                self._pipelines.append(pipeline)
                self._captures.append(self._audio.open_capture(device, pipeline.queue))
                self.device_names.append(f"{speaker}: {device.name}")
        except Exception:  # не оставляем открытыми уже запущенные потоки и устройства
            self.stop()
            raise
        self.saver.save()

    def _handle_text(self, event: TextEvent) -> None:
        if event.kind != "final":
            self._on_text(event)
            return
        utterance = Utterance(event.speaker, event.start, event.text, event.end)
        with self._lock:
            if self._settings.echo_filter and self._drop_echo(utterance):
                self._on_text(replace(event, kind="discard", text=""))
                return
            self.transcript.add(utterance)
            self.saver.save()
        self._on_text(event)

    def _drop_echo(self, utterance: Utterance) -> bool:
        """True — реплика микрофона оказалась эхом колонок и не нужна.

        Финалы приходят из двух потоков в любом порядке, поэтому проверяем в обе стороны:
        новую фразу микрофона против собеседника и старые фразы микрофона против новой фразы собеседника.
        """
        current = self.transcript.utterances
        others = [u for u in current if u.speaker == SPEAKER_OTHERS]
        if utterance.speaker == SPEAKER_ME:
            return is_echo(utterance, others)
        others.append(utterance)
        echoes = [u for u in current if u.speaker == SPEAKER_ME and is_echo(u, others)]
        if echoes:
            self.transcript.remove(echoes)
        return False

    def stop(self) -> Path | None:
        if self._closed:
            return self.saver.path
        self._closed = True
        # каждый шаг отдельно: сбой одного устройства не должен мешать сохранить текст
        steps = [*(c.stop for c in self._captures), *(p.stop for p in self._pipelines), self._audio.close]
        for step in steps:
            try:
                step()
            except Exception:
                log.exception("Ошибка при остановке записи")
        self._captures.clear()
        self._pipelines.clear()
        with self._lock:
            return self.saver.save()

    def save(self) -> Path | None:
        with self._lock:
            return self.saver.save()
