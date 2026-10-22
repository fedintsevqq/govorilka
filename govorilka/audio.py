"""Захват звука: выбирает реализацию под текущую ОС."""

import sys

from govorilka.audio_base import CHUNK_SECONDS, AudioChunk, AudioError, Capture, DeviceInfo, to_mono_16k

if sys.platform == "darwin":
    from govorilka.audio_mac import AudioSystem
else:
    from govorilka.audio_windows import AudioSystem

__all__ = ["CHUNK_SECONDS", "AudioChunk", "AudioError", "AudioSystem", "Capture", "DeviceInfo", "to_mono_16k"]
