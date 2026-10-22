"""Захват звука на Windows: микрофон и всё, что играет в колонках/наушниках (WASAPI loopback)."""

from __future__ import annotations

import logging
import queue
import time

import pyaudiowpatch as pyaudio

from govorilka.audio_base import CHUNK_SECONDS, AudioChunk, AudioError, DeviceInfo, to_mono_16k

log = logging.getLogger(__name__)


class AudioSystem:
    """Обёртка над PyAudio: поиск устройств и открытие потоков."""

    def __init__(self) -> None:
        self._pa = pyaudio.PyAudio()

    def close(self) -> None:
        self._pa.terminate()

    def default_mic(self) -> DeviceInfo:
        try:
            info = self._pa.get_default_wasapi_device(d_out=False)
        except (OSError, LookupError):
            try:
                info = self._pa.get_default_input_device_info()
            except OSError as exc:
                raise AudioError(f"Не нашёл микрофон: {exc}") from exc
        return _to_device(info)

    def default_loopback(self) -> DeviceInfo:
        try:
            return _to_device(self._pa.get_default_wasapi_loopback())
        except (OSError, LookupError) as exc:
            raise AudioError(f"Не нашёл loopback-устройство для звука компьютера: {exc}") from exc

    def open_capture(self, device: DeviceInfo, out: queue.Queue[AudioChunk]) -> "Capture":
        return Capture(self._pa, device, out)


def _to_device(info: dict) -> DeviceInfo:
    return DeviceInfo(
        index=int(info["index"]),
        name=str(info["name"]),
        rate=int(info["defaultSampleRate"]),
        channels=max(1, min(2, int(info["maxInputChannels"]))),
    )


class Capture:
    """Поток с устройства; куски звука складываются в очередь."""

    def __init__(self, pa: pyaudio.PyAudio, device: DeviceInfo, out: queue.Queue[AudioChunk]) -> None:
        self.device = device
        self._out = out
        frames = int(device.rate * CHUNK_SECONDS)
        try:
            self._stream = pa.open(
                format=pyaudio.paInt16,
                channels=device.channels,
                rate=device.rate,
                input=True,
                input_device_index=device.index,
                frames_per_buffer=frames,
                stream_callback=self._callback,
            )
        except OSError as exc:
            raise AudioError(f"Не открыл устройство «{device.name}»: {exc}") from exc

    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        try:
            samples = to_mono_16k(in_data, self.device.channels, self.device.rate)
            start = time.monotonic() - frame_count / self.device.rate
            self._out.put_nowait(AudioChunk(samples, start))
        except queue.Full:
            log.warning("Очередь звука переполнена, кусок пропущен (%s)", self.device.name)
        except Exception:  # колбэк PortAudio не должен падать
            log.exception("Ошибка обработки звука с %s", self.device.name)
        return (None, pyaudio.paContinue)

    def stop(self) -> None:
        try:
            self._stream.stop_stream()
        except OSError as exc:
            log.warning("Ошибка при остановке %s: %s", self.device.name, exc)
        finally:
            try:
                self._stream.close()
            except OSError as exc:
                log.warning("Ошибка при закрытии %s: %s", self.device.name, exc)
