"""Захват звука на macOS через помощника на Swift (mac/audiotap.swift).

`--source system` — весь звук системы через ScreenCaptureKit (macOS 13+),
`--source mic` — микрофон по умолчанию через AVAudioEngine.
Помощник сам переподключается, если сменили наушники или микрофон.
В stdout идёт сырой float32 моно 48 кГц, в stderr — служебные строки.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time

import numpy as np

from govorilka.audio_base import (
    CHUNK_SECONDS,
    AudioChunk,
    AudioError,
    DeviceInfo,
    PermissionDeniedError,
    StreamResampler,
)
from govorilka.config import AUDIOTAP_PATH

log = logging.getLogger(__name__)

TAP_RATE = 48_000
TAP_READY_TIMEOUT_S = 30  # на первом запуске помощник ждёт, пока пользователь ответит на системный запрос
PERMISSION_PREFIX = "permission:"  # так помощник сообщает «нет доступа»: permission: screen|microphone: подробности
SERVICES = {"screen": "ScreenCapture", "microphone": "Microphone"}
PERMISSION_TEXT = {
    "screen": (
        "Нет доступа к записи системного звука. Разрешите Говорилке доступ в «Системные настройки → "
        "Конфиденциальность и безопасность → Запись экрана и системного звука» и перезапустите приложение."
    ),
    "microphone": (
        "Нет доступа к микрофону. Разрешите Говорилке доступ в «Системные настройки → "
        "Конфиденциальность и безопасность → Микрофон» и перезапустите приложение."
    ),
}
SYSTEM_DEVICE = DeviceInfo(index=-1, name="Звук системы (ScreenCaptureKit)", rate=TAP_RATE, channels=1)
MIC_DEVICE = DeviceInfo(index=-2, name="Микрофон по умолчанию", rate=TAP_RATE, channels=1)


class AudioSystem:
    def close(self) -> None:
        pass

    def _require_helper(self) -> None:
        if not AUDIOTAP_PATH.is_file():
            raise AudioError(f"Не найден помощник захвата звука: {AUDIOTAP_PATH}")

    def default_mic(self) -> DeviceInfo:
        self._require_helper()
        return MIC_DEVICE

    def default_loopback(self) -> DeviceInfo:
        self._require_helper()
        return SYSTEM_DEVICE

    def open_capture(self, device: DeviceInfo, out: queue.Queue[AudioChunk]) -> HelperCapture:
        if device.index == SYSTEM_DEVICE.index:
            return HelperCapture("system", device, out)
        return HelperCapture("mic", device, out)

    @staticmethod
    def default_signature() -> None:
        """Смену устройств помощник отслеживает сам, следить снаружи не нужно."""
        return None


def failure_for(device_name: str, error: str) -> AudioError:
    """Превращает строку ошибки помощника в понятное исключение.

    Подсказку про разрешения показываем только если помощник сам сказал, что доступа нет.
    Любая другая причина выводится как есть, чтобы её можно было разобрать.
    """
    if error.startswith(PERMISSION_PREFIX):
        kind, _, details = error.removeprefix(PERMISSION_PREFIX).strip().partition(":")
        kind = kind.strip()
        if kind in SERVICES:
            log.warning("Нет разрешения (%s): %s", kind, details.strip())
            return PermissionDeniedError(SERVICES[kind], PERMISSION_TEXT[kind])
    return AudioError(f"{device_name} недоступен: {error}. Подробности в govorilka.log")


class HelperCapture:
    """Запускает помощника и читает из него звук в отдельном потоке."""

    def __init__(self, source: str, device: DeviceInfo, out: queue.Queue[AudioChunk]) -> None:
        self.device = device
        self._out = out
        self._ready = threading.Event()
        self._error: str | None = None
        try:
            self._proc = subprocess.Popen(
                [str(AUDIOTAP_PATH), "--source", source],
                stdin=subprocess.PIPE,  # помощник завершится сам, если мы упадём и pipe закроется
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise AudioError(f"Не запустил помощника захвата звука: {exc}") from exc
        threading.Thread(target=self._read_stderr, name=f"audiotap-{source}-log", daemon=True).start()
        if not self._ready.wait(TAP_READY_TIMEOUT_S) or self._error:
            error = self._error or "помощник не ответил"
            self.stop()
            raise failure_for(device.name, error)
        threading.Thread(target=self._read_audio, name=f"audiotap-{source}", daemon=True).start()

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in self._proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line == "ready":
                self._ready.set()
            elif line.startswith("error:"):
                self._error = line.removeprefix("error:").strip()
                log.warning("audiotap: %s", line)  # настоящая причина должна попасть в govorilka.log
                self._ready.set()
            elif line:
                log.info("audiotap: %s", line)
        if not self._ready.is_set():
            self._error = self._error or "помощник завершился"
            self._ready.set()

    def _read_audio(self) -> None:
        assert self._proc.stdout is not None
        frame_bytes = int(TAP_RATE * CHUNK_SECONDS) * 4
        resampler = StreamResampler(TAP_RATE)
        while True:
            data = self._proc.stdout.read(frame_bytes)
            if not data:
                break
            usable = len(data) - len(data) % 4
            samples = np.frombuffer(data[:usable], dtype=np.float32)
            start = time.monotonic() - len(samples) / TAP_RATE
            try:
                self._out.put_nowait(AudioChunk(resampler.process(samples), start))
            except queue.Full:
                log.warning("Очередь звука переполнена, кусок пропущен (%s)", self.device.name)

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def stop(self) -> None:
        try:
            if self._proc.poll() is None:
                try:
                    if self._proc.stdin:
                        self._proc.stdin.close()
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    self._proc.kill()
        finally:
            # закрываем трубы всегда, даже если помощник уже упал: иначе при каждом переподключении течёт по 3 дескриптора
            for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                try:
                    if pipe:
                        pipe.close()
                except OSError:
                    pass
