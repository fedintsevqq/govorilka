"""Расшифровка готовых аудио- и видеофайлов.

Звук достаёт PyAV (внутри ffmpeg), поэтому подходят почти любые форматы.
Файл читается потоком и режется на фразы тем же сегментером, что и живая
запись, так что длина файла ограничена только временем. Голоса различаются
так же, как у собеседников в созвоне.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from govorilka.config import SAMPLE_RATE, SOURCE_FILE, SPEAKER_FILE, Settings
from govorilka.engines import Engines
from govorilka.pipeline import PhraseProcessor, TextEvent
from govorilka.segmenter import Segmenter
from govorilka.speakers import Diarizer
from govorilka.transcript import AutoSaver, Transcript, Utterance

log = logging.getLogger(__name__)

AUDIO_FORMATS = (".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".wma")
VIDEO_FORMATS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".wmv")
SUPPORTED_FORMATS = AUDIO_FORMATS + VIDEO_FORMATS
SAVE_EVERY_S = 5.0
CANCELLED = "Отменено"
FILE_DIALOG_TYPES = [
    ("Аудио и видео", " ".join(f"*{ext}" for ext in SUPPORTED_FORMATS)),
    ("Аудио", " ".join(f"*{ext}" for ext in AUDIO_FORMATS)),
    ("Видео", " ".join(f"*{ext}" for ext in VIDEO_FORMATS)),
    ("Все файлы", "*.*"),
]


class FileJobError(RuntimeError):
    pass


@dataclass(frozen=True)
class DecodedChunk:
    samples: np.ndarray  # float32 моно 16 кГц
    t: float  # секунды от начала файла


@dataclass
class DecodedAudio:
    duration: float  # секунды, 0 — неизвестна
    chunks: Iterator[DecodedChunk]
    close: Callable[[], None]  # закрывает файл; безопасно звать повторно, нужно и если чтение не началось


def decode_audio(path: Path) -> DecodedAudio:
    """Открывает аудио или видео и отдаёт звук кусками. Файл закрывайте через .close()."""
    import av

    try:
        container = av.open(str(path))
    except av.FFmpegError as exc:
        raise FileJobError(f"Не открыл файл: {exc}") from exc
    if not container.streams.audio:
        container.close()
        raise FileJobError("В файле нет звуковой дорожки")
    stream = container.streams.audio[0]
    duration = float(container.duration / av.time_base) if container.duration else 0.0

    def chunks() -> Iterator[DecodedChunk]:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        position = 0
        try:
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    samples = out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                    yield DecodedChunk(samples, position / SAMPLE_RATE)
                    position += len(samples)
            for out in resampler.resample(None):  # хвост, оставшийся в ресемплере
                samples = out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                yield DecodedChunk(samples, position / SAMPLE_RATE)
                position += len(samples)
        except av.FFmpegError as exc:
            raise FileJobError(f"Файл повреждён или не читается: {exc}") from exc
        finally:
            container.close()

    return DecodedAudio(duration, chunks(), container.close)


class FileJob:
    """Фоновая расшифровка одного файла: прогресс, отмена, автосохранение."""

    def __init__(
        self,
        path: Path,
        settings: Settings,
        engines: Engines,
        on_text: Callable[[TextEvent], None],
        on_progress: Callable[[float], None],
        on_done: Callable[[Path | None, str | None], None],
    ) -> None:
        self.source_path = path
        self.transcript = Transcript(datetime.now(), path.stem)
        self.saver = AutoSaver(self.transcript, settings.transcripts_path)
        self._engines = engines
        self._diarize = settings.split_speakers
        self._on_text = on_text
        self._on_progress = on_progress
        self._on_done = on_done
        self._cancel = threading.Event()
        self._last_save = 0.0
        self.diarizer: Diarizer | None = None
        self._thread = threading.Thread(target=self._run, name="file-job", daemon=True)

    @property
    def path(self) -> Path:
        return self.saver.path

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self) -> None:
        self._thread.join()

    def _run(self) -> None:
        error: str | None = None
        try:
            self._transcribe()
        except FileJobError as exc:
            error = str(exc)
        except Exception as exc:
            log.exception("Ошибка расшифровки файла %s", self.source_path)
            error = f"Ошибка расшифровки: {exc}"
        if self._cancel.is_set() and error is None:
            error = CANCELLED
        # даже при ошибке сохраняем всё, что успели расшифровать
        saved = self.saver.save() if self.transcript.utterances else None
        if saved is None and self.transcript.utterances and error is None:
            error = f"Не смог сохранить транскрипт: {self.saver.path}"
        self._on_done(saved, error)

    def _transcribe(self) -> None:
        audio = decode_audio(self.source_path)
        try:
            self._transcribe_audio(audio)
        finally:
            audio.close()  # иначе при ошибке до начала чтения файл остаётся занятым до сборки мусора

    def _transcribe_audio(self, audio: DecodedAudio) -> None:
        duration, chunks = audio.duration, audio.chunks
        diarizer = Diarizer(self._engines.speakers.embed) if self._diarize else None
        self.diarizer = diarizer
        processor = PhraseProcessor(SOURCE_FILE, SPEAKER_FILE, self._engines.recognizer, self._collect, diarizer)
        segmenter = Segmenter(detector=self._engines.vad.detector())
        reported = -1.0
        for chunk in chunks:
            if self._cancel.is_set():
                break
            for event in segmenter.feed(chunk.samples, chunk.t):
                if event.kind != "partial":  # черновики не нужны: файл читается быстрее, чем звучит
                    processor.handle(event)
            if duration and chunk.t - reported >= 1.0:
                reported = chunk.t
                self._on_progress(min(1.0, chunk.t / duration))
        for event in segmenter.flush():
            processor.handle(event)
        self._on_progress(1.0)

    def _collect(self, event: TextEvent) -> None:
        if event.kind == "final":
            self.transcript.add(Utterance(event.speaker, event.start, event.text, event.end, event.source, event.speaker))
            # файл расшифровывается быстро — сохраняем не после каждой фразы, а раз в несколько секунд
            if time.monotonic() - self._last_save >= SAVE_EVERY_S:
                self.saver.save()
                self._last_save = time.monotonic()
        self._on_text(event)
