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
TAP_READY_TIMEOUT_S = 30  # обычный запуск помощника
PERMISSION_WAIT_S = 120  # помощник сказал «waiting: permission»: человек читает системный запрос — ждём дольше
MIC_STALL_S = 6.0  # микрофон шлёт звук непрерывно (даже тишину): дольше без данных — помощник завис
SILENT_PEAK = 1e-4  # тише этого — фактически ноль: микрофон «молчит» (AirPods в неверном режиме, выключенный вход)
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
    def __init__(self) -> None:
        self._prompted: set[str] = set()  # системный запрос показываем только при первом открытии, не при переподключениях
        self.cancel: threading.Event | None = None  # сессия останавливается: не ждать помощника 30 секунд

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
        source = "system" if device.index == SYSTEM_DEVICE.index else "mic"
        prompt = source not in self._prompted
        self._prompted.add(source)
        return HelperCapture(source, device, out, prompt=prompt, cancel=self.cancel)

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

    def __init__(
        self,
        source: str,
        device: DeviceInfo,
        out: queue.Queue[AudioChunk],
        prompt: bool = False,
        cancel: threading.Event | None = None,
    ) -> None:
        self.device = device
        self.source = source
        self._out = out
        self._ready = threading.Event()
        self._error: str | None = None
        self._waiting_permission = False
        self.permission_error: PermissionDeniedError | None = None  # помощник упал уже во время записи из-за доступа
        self._last_data = 0.0  # когда пришёл последний кусок звука (0 — ещё не было)
        self._loud_at = time.monotonic()  # когда звук последний раз был громче нуля
        command = [str(AUDIOTAP_PATH), "--source", source] + (["--prompt"] if prompt else [])
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,  # помощник завершится сам, если мы упадём и pipe закроется
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise AudioError(f"Не запустил помощника захвата звука: {exc}") from exc
        threading.Thread(target=self._read_stderr, name=f"audiotap-{source}-log", daemon=True).start()
        started = time.monotonic()
        while not self._ready.wait(0.2):
            if cancel is not None and cancel.is_set():  # запись остановили, пока помощник запускался
                self.stop()
                raise AudioError(f"{device.name}: запуск отменён")
            limit = PERMISSION_WAIT_S if self._waiting_permission else TAP_READY_TIMEOUT_S
            if time.monotonic() - started > limit:
                break
        if not self._ready.is_set() or self._error:
            if self._error:
                error = self._error
            elif self._waiting_permission:
                error = "вы не ответили на системный запрос доступа. Ответьте на него и нажмите запись ещё раз"
            else:
                error = "помощник не ответил"
            self.stop()
            raise failure_for(device.name, error)
        threading.Thread(target=self._read_audio, name=f"audiotap-{source}", daemon=True).start()

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in self._proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line == "ready":
                self._ready.set()
            elif line == "waiting: permission":
                self._waiting_permission = True
                log.info("audiotap (%s): ждёт ответа на системный запрос доступа", self.source)
            elif line.startswith("error:"):
                error = line.removeprefix("error:").strip()
                log.warning("audiotap: %s", line)  # настоящая причина должна попасть в govorilka.log
                if self._ready.is_set():  # упал уже во время записи: сессия решит, переподключать ли
                    failure = failure_for(self.device.name, error)
                    if isinstance(failure, PermissionDeniedError):
                        self.permission_error = failure
                else:
                    self._error = error
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
            now = time.monotonic()
            self._last_data = now
            if samples.size and float(np.max(np.abs(samples))) > SILENT_PEAK:
                self._loud_at = now
            start = now - len(samples) / TAP_RATE
            try:
                self._out.put_nowait(AudioChunk(resampler.process(samples), start))
            except queue.Full:
                log.warning("Очередь звука переполнена, кусок пропущен (%s)", self.device.name)

    def is_alive(self) -> bool:
        """Помощник жив и (для микрофона) шлёт звук. Завис без данных — считаем упавшим: сессия переподключит."""
        if self._proc.poll() is not None:
            return False
        stalled = self.source == "mic" and self._last_data and time.monotonic() - self._last_data > MIC_STALL_S
        if stalled:
            log.warning("audiotap (mic): нет звука %.0f с — переподключаю", time.monotonic() - self._last_data)
        return not stalled

    def silent_for(self) -> float:
        """Сколько секунд микрофон отдаёт только нули (AirPods в неверном режиме, выключенный вход)."""
        return time.monotonic() - self._loud_at if self._last_data else 0.0

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
