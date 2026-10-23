"""Одна запись созвона: устройства, потоки распознавания, транскрипт и автосохранение.

Пока идёт запись, сторож раз в пару секунд проверяет устройства. Если в системе
выбрали другие наушники или микрофон либо устройство пропало, запись
переподключается к новому устройству, не теряя транскрипт.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from govorilka.audio import AudioError, AudioSystem, Capture
from govorilka.audio_base import PermissionDeniedError
from govorilka.config import SOURCE_MIC, SOURCE_SYSTEM, SPEAKER_ME, SPEAKER_OTHERS, Settings
from govorilka.echo import is_echo
from govorilka.engines import Engines
from govorilka.pipeline import LevelCallback, PhraseProcessor, SourcePipeline, TextEvent
from govorilka.speakers import Diarizer
from govorilka.transcript import AutoSaver, Transcript, Utterance

log = logging.getLogger(__name__)

WATCH_EVERY_S = 2.0
QUEUE_WARN_AT = 400  # из 600 кусков по 0.1 с, то есть примерно 40 секунд отставания
RETRY_AFTER_S = 10.0  # как часто пробовать вернуть упавшее устройство, если выбор в системе не менялся


@dataclass
class _Source:
    name: str  # SOURCE_MIC / SOURCE_SYSTEM
    find_device: str  # метод AudioSystem: default_mic / default_loopback
    pipeline: SourcePipeline
    capture: Capture | None = None


class Session:
    def __init__(
        self,
        settings: Settings,
        engines: Engines,
        title: str,
        on_text: Callable[[TextEvent], None],
        on_level: LevelCallback,
        on_notice: Callable[[str], None] = lambda _: None,
    ) -> None:
        if not (settings.capture_mic or settings.capture_system):
            raise AudioError("Включите хотя бы один источник: микрофон или звук компьютера")
        self.transcript = Transcript(datetime.now(), title)
        self._settings = settings
        self._engines = engines
        self._on_text = on_text
        self._on_level = on_level
        self._on_notice = on_notice
        self.saver = AutoSaver(self.transcript, settings.transcripts_path, self._on_save_state)
        self._audio: AudioSystem | None = AudioSystem()
        self._sources: list[_Source] = []
        self._signature = AudioSystem.default_signature()
        self._watch_stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._device_lock = threading.Lock()  # переподключение и остановка не должны пересекаться
        self._closed = False
        self._last_retry = 0.0
        self._last_notice = ""
        self._lock = threading.Lock()
        self.device_names: list[str] = []

    @property
    def path(self) -> Path:
        return self.saver.path

    def start(self) -> None:
        t0 = time.monotonic()
        try:
            if self._settings.capture_mic:
                self._add_source(SOURCE_MIC, SPEAKER_ME, "default_mic", t0, diarize=False)
            if self._settings.capture_system:
                self._add_source(SOURCE_SYSTEM, SPEAKER_OTHERS, "default_loopback", t0, self._settings.split_speakers)
            errors = self._open_captures()
            if errors:  # при старте любая неудача — ошибка
                # отказ в доступе отдаём как есть: окно предложит сбросить разрешение
                denied = next((exc for _, exc in errors if isinstance(exc, PermissionDeniedError)), None)
                raise denied or AudioError("; ".join(f"{speaker}: {exc}" for speaker, exc in errors))
        except Exception:  # не оставляем открытыми уже запущенные потоки и устройства
            self.stop()
            raise
        self._watcher = threading.Thread(target=self._watch_devices, name="device-watcher", daemon=True)
        self._watcher.start()
        self.saver.save()

    def _add_source(self, name: str, speaker: str, find_device: str, t0: float, diarize: bool) -> None:
        diarizer = Diarizer(self._engines.speakers.embed) if diarize else None
        processor = PhraseProcessor(
            name, speaker, self._engines.recognizer, self._handle_text, diarizer, on_problem=self._notify
        )
        pipeline = SourcePipeline(processor, self._engines.vad.detector(), t0, self._on_level)
        pipeline.start()
        self._sources.append(_Source(name, find_device, pipeline))

    def _open_captures(self) -> list[tuple[str, AudioError]]:
        """Открывает то, что ещё не открыто. Сбой одного источника не мешает другому."""
        if self._audio is None:
            self._audio = AudioSystem()
        errors = []
        for source in self._sources:
            if source.capture is not None:
                continue
            if self._watch_stop.is_set():  # идёт остановка — не тратим время на новые устройства
                break
            try:
                device = getattr(self._audio, source.find_device)()
                source.capture = self._audio.open_capture(device, source.pipeline.queue)
            except AudioError as exc:
                errors.append((source.pipeline.processor.speaker, exc))
        self.device_names = [
            f"{s.pipeline.processor.speaker}: {s.capture.device.name}" for s in self._sources if s.capture
        ]
        return errors

    # ---------- смена устройств ----------

    def _watch_devices(self) -> None:
        while not self._watch_stop.wait(WATCH_EVERY_S):
            try:
                self._check_devices()
            except Exception:  # сторож не должен умирать — без него запись не переживёт смену наушников
                log.exception("Ошибка проверки устройств")

    def _on_save_state(self, error: str | None) -> None:
        if error:
            self._notify(f"Не могу сохранить транскрипт ({error}). Освободите место или смените папку в настройках.")
        else:
            self._notify("Сохранение транскрипта снова работает")

    def _check_backlog(self) -> None:
        """Распознавание не успевает за речью: очередь звука почти полная, дальше звук начнёт теряться."""
        if any(s.pipeline.queue.qsize() > QUEUE_WARN_AT for s in self._sources):
            self._notify("Распознавание не успевает за речью: часть звука может потеряться. Закройте лишние программы.")

    def _check_devices(self) -> None:
        self._check_backlog()
        signature = AudioSystem.default_signature()
        if signature is not None and signature != self._signature:
            # в системе выбрали другое устройство: PortAudio читает список устройств только при запуске,
            # поэтому переоткрываем звуковую систему целиком
            self._signature = signature
            self._reconnect(full=True, reason="сменилось устройство")
            return
        broken = any(s.capture is None or not s.capture.is_alive() for s in self._sources)
        if broken and time.monotonic() - self._last_retry >= RETRY_AFTER_S:
            self._reconnect(full=False, reason="устройство пропало")  # исправные источники не трогаем

    def _reconnect(self, full: bool, reason: str) -> None:
        with self._device_lock:
            if self._closed:
                return
            self._last_retry = time.monotonic()
            if full:
                self._close_captures(everything=True)
                self._audio = None
            else:
                self._close_captures(everything=False)
            try:
                errors = self._open_captures()
            except Exception as exc:  # например, не поднялась звуковая система
                log.exception("Не удалось переподключить устройства")
                errors = [("", AudioError(str(exc)))]
        if errors:
            self._notify(f"Запись: {reason}, устройство пока недоступно ({'; '.join(f'{speaker}: {exc}' if speaker else str(exc) for speaker, exc in errors)}). Пробую снова…")
        else:
            self._notify(f"Запись: {reason}, переключился → {'; '.join(self.device_names)}")

    def _notify(self, message: str) -> None:
        if message != self._last_notice:  # одно и то же сообщение не повторяем при каждой попытке
            self._last_notice = message
            self._on_notice(message)

    def _close_captures(self, everything: bool) -> None:
        """everything=False закрывает только упавшие источники и оставляет звуковую систему."""
        for source in self._sources:
            if source.capture is None or (not everything and source.capture.is_alive()):
                continue
            try:
                source.capture.stop()
            except Exception:
                log.exception("Ошибка при закрытии устройства")
            source.capture = None
        if everything and self._audio is not None:
            try:
                self._audio.close()
            except Exception:
                log.exception("Ошибка при закрытии звуковой системы")

    # ---------- текст ----------

    def _handle_text(self, event: TextEvent) -> None:
        if event.kind != "final":
            self._on_text(event)
            return
        utterance = Utterance(event.speaker, event.start, event.text, event.end, event.source)
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
        новую фразу микрофона против собеседников и старые фразы микрофона против новой фразы собеседника.
        """
        current = self.transcript.utterances
        others = [u for u in current if u.source == SOURCE_SYSTEM]
        if utterance.source == SOURCE_MIC:
            return is_echo(utterance, others)
        others.append(utterance)
        echoes = [u for u in current if u.source == SOURCE_MIC and is_echo(u, others)]
        if echoes:
            self.transcript.remove(echoes)
        return False

    # ---------- остановка ----------

    def stop(self) -> Path | None:
        self._watch_stop.set()
        if self._watcher and self._watcher is not threading.current_thread():
            self._watcher.join()
        with self._device_lock:
            if self._closed:
                return self.saver.path
            self._closed = True
            self._close_captures(everything=True)
        # каждый поток отдельно: сбой одного не должен мешать сохранить текст
        for source in self._sources:
            try:
                source.pipeline.stop()
            except Exception:
                log.exception("Ошибка при остановке распознавания")
        with self._lock:
            return self.saver.save()

    def save(self) -> Path | None:
        with self._lock:
            return self.saver.save()
