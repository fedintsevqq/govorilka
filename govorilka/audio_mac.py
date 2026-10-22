"""Захват звука на macOS.

Микрофон — через sounddevice (PortAudio). Системный звук macOS напрямую не отдаёт,
поэтому его пишет маленький помощник на Swift (ScreenCaptureKit, macOS 13+):
он выводит сырой float32 моно 48 кГц в stdout, а служебные строки — в stderr.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time

import numpy as np
import sounddevice as sd

from govorilka.audio_base import CHUNK_SECONDS, AudioChunk, AudioError, DeviceInfo, resample_mono
from govorilka.config import AUDIOTAP_PATH

log = logging.getLogger(__name__)

TAP_RATE = 48_000
TAP_READY_TIMEOUT_S = 20
PERMISSION_HINT = (
    "Разрешите доступ в «Системные настройки → Конфиденциальность и безопасность → "
    "Запись экрана и системного звука» для Говорилки (или Терминала, если запускаете из него), "
    "затем перезапустите приложение."
)
SYSTEM_DEVICE = DeviceInfo(index=-1, name="Звук системы (ScreenCaptureKit)", rate=TAP_RATE, channels=1)


class AudioSystem:
    def close(self) -> None:
        pass

    def default_mic(self) -> DeviceInfo:
        try:
            info = sd.query_devices(kind="input")
        except (sd.PortAudioError, ValueError) as exc:
            raise AudioError(f"Не нашёл микрофон: {exc}") from exc
        return DeviceInfo(
            index=int(info["index"]),
            name=str(info["name"]),
            rate=int(info["default_samplerate"]),
            channels=max(1, min(2, int(info["max_input_channels"]))),
        )

    def default_loopback(self) -> DeviceInfo:
        if not AUDIOTAP_PATH.is_file():
            raise AudioError(f"Не найден помощник для системного звука: {AUDIOTAP_PATH}")
        return SYSTEM_DEVICE

    def open_capture(self, device: DeviceInfo, out: queue.Queue[AudioChunk]) -> MicCapture | SystemCapture:
        if device.index == SYSTEM_DEVICE.index:
            return SystemCapture(out)
        return MicCapture(device, out)


def _put(out: queue.Queue[AudioChunk], chunk: AudioChunk, name: str) -> None:
    try:
        out.put_nowait(chunk)
    except queue.Full:
        log.warning("Очередь звука переполнена, кусок пропущен (%s)", name)


class MicCapture:
    def __init__(self, device: DeviceInfo, out: queue.Queue[AudioChunk]) -> None:
        self.device = device
        self._out = out
        try:
            self._stream = sd.InputStream(
                device=device.index,
                channels=device.channels,
                samplerate=device.rate,
                dtype="float32",
                blocksize=int(device.rate * CHUNK_SECONDS),
                callback=self._callback,
            )
            self._stream.start()
        except sd.PortAudioError as exc:
            raise AudioError(f"Не открыл микрофон «{device.name}»: {exc}. Проверьте доступ к микрофону.") from exc

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        try:
            mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
            start = time.monotonic() - frames / self.device.rate
            _put(self._out, AudioChunk(resample_mono(mono, self.device.rate), start), self.device.name)
        except Exception:  # колбэк PortAudio не должен падать
            log.exception("Ошибка обработки звука с %s", self.device.name)

    def stop(self) -> None:
        try:
            self._stream.stop()
        except sd.PortAudioError as exc:
            log.warning("Ошибка при остановке микрофона: %s", exc)
        finally:
            self._stream.close()


class SystemCapture:
    """Запускает помощника и читает из него звук в отдельном потоке."""

    def __init__(self, out: queue.Queue[AudioChunk]) -> None:
        self.device = SYSTEM_DEVICE
        self._out = out
        self._ready = threading.Event()
        self._error: str | None = None
        try:
            self._proc = subprocess.Popen(
                [str(AUDIOTAP_PATH)],
                stdin=subprocess.PIPE,  # помощник завершится сам, если мы упадём и pipe закроется
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise AudioError(f"Не запустил помощника системного звука: {exc}") from exc
        threading.Thread(target=self._read_stderr, name="audiotap-stderr", daemon=True).start()
        if not self._ready.wait(TAP_READY_TIMEOUT_S) or self._error:
            self.stop()
            raise AudioError(f"Системный звук недоступен: {self._error or 'помощник не ответил'}. {PERMISSION_HINT}")
        threading.Thread(target=self._read_audio, name="audiotap-audio", daemon=True).start()

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in self._proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line == "ready":
                self._ready.set()
            elif line.startswith("error:"):
                self._error = line.removeprefix("error:").strip()
                self._ready.set()
            elif line:
                log.info("audiotap: %s", line)
        if not self._ready.is_set():
            self._error = self._error or "помощник завершился"
            self._ready.set()

    def _read_audio(self) -> None:
        assert self._proc.stdout is not None
        frame_bytes = int(TAP_RATE * CHUNK_SECONDS) * 4
        while True:
            data = self._proc.stdout.read(frame_bytes)
            if not data:
                break
            usable = len(data) - len(data) % 4
            samples = np.frombuffer(data[:usable], dtype=np.float32)
            start = time.monotonic() - len(samples) / TAP_RATE
            _put(self._out, AudioChunk(resample_mono(samples, TAP_RATE), start), self.device.name)

    def stop(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()
