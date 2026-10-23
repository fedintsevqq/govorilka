"""Все модели приложения: грузятся один раз и живут до выхода."""

from __future__ import annotations

from dataclasses import dataclass

from govorilka import models
from govorilka.recognizer import Recognizer, RecognizerError
from govorilka.speakers import SpeakerModel
from govorilka.vad import SileroModel


@dataclass(frozen=True)
class Engines:
    recognizer: Recognizer
    vad: SileroModel
    speakers: SpeakerModel


def load_engines(model_name: str, on_status: models.StatusCallback, previous: Engines | None = None) -> Engines:
    """Грузит то, чего ещё нет. При смене модели распознавания детектор и голоса не перегружаются."""
    if previous and previous.recognizer.model_name == model_name:
        return previous
    try:
        vad = previous.vad if previous else SileroModel(on_status)
        speakers = previous.speakers if previous else SpeakerModel(on_status)
        recognizer = Recognizer(model_name, on_status)
    except RecognizerError:
        raise
    except Exception as exc:  # сеть, диск, битый файл модели — любая причина должна дойти до окна
        raise RecognizerError(f"Не загрузил модели: {exc}. Проверьте интернет и свободное место на диске.") from exc
    on_status("Модели готовы")
    return Engines(recognizer, vad, speakers)
