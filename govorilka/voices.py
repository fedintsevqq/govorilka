"""Память голосов: по желанию пользователя Говорилка запоминает, как звучат знакомые, и узнаёт их в следующих созвонах.

Это биометрические данные, поэтому: выключено по умолчанию; хранится только числовой отпечаток (256 чисел), записи
голоса нет; файл лежит локально в папке данных и никуда не отправляется; всё стирается кнопкой в настройках.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from govorilka.transcript import write_atomic

log = logging.getLogger(__name__)

RECOGNIZE_AT = 0.62  # близость, с которой узнаём знакомого (строже, чем «тот же человек в этом созвоне»: 0.5)
MAX_VOICES = 200
VERSION = 1


@dataclass
class KnownVoice:
    name: str
    vector: np.ndarray
    count: int = 1  # по скольким созвонам усреднён отпечаток


class VoiceBook:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.voices: list[KnownVoice] = []
        self.load_error: Path | None = None  # куда отложили битый файл; None — всё прочиталось
        self._lock = threading.RLock()  # книгу трогают и окно (имена, «забыть»), и рабочий поток (уточнение)
        self._load()

    # ---------- хранение ----------

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("voices", [])
            if not isinstance(entries, list):
                raise ValueError("voices должен быть списком")
        except FileNotFoundError:
            self.voices = []
            return
        except (OSError, ValueError, AttributeError):
            self._quarantine()
            return
        voices, skipped = [], 0
        for entry in entries:
            try:
                vector = np.asarray(entry["vector"], dtype=np.float32).reshape(-1)
                if vector.size == 0 or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                    raise ValueError("пустой или испорченный отпечаток")
                voices.append(KnownVoice(str(entry["name"]), _unit(vector), int(entry.get("count", 1))))
            except (KeyError, TypeError, ValueError):
                skipped += 1  # одна плохая запись не должна стоить всей памяти
        if skipped:
            log.warning("В памяти голосов пропущено повреждённых записей: %d", skipped)
        self.voices = voices

    def _quarantine(self) -> None:
        log.exception("Не прочитал память голосов %s", self.path)
        self.voices = []
        # копия с меткой времени: вторая порча не должна затереть первую, там могут быть чьи-то голоса
        copy = self.path.with_name(f"{self.path.stem}-{datetime.now():%Y%m%d-%H%M%S}.broken")
        try:
            self.path.replace(copy)
            self.load_error = copy
        except OSError:
            self.load_error = self.path

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": VERSION,
                "voices": [
                    {"name": v.name, "count": v.count, "vector": [round(float(x), 5) for x in v.vector]}
                    for v in self.voices
                ],
            }
            write_atomic(self.path, json.dumps(payload, ensure_ascii=False))

    # ---------- использование ----------

    def rank(self, vector: np.ndarray) -> list[tuple[str, float]]:
        """Все знакомые, достаточно похожие на этот голос, от самого похожего."""
        vector = _unit(vector)
        if not np.isfinite(vector).all() or not np.linalg.norm(vector):
            return []
        with self._lock:
            scored = [
                (v.name, float(vector @ v.vector))
                for v in self.voices
                if v.vector.size == vector.size  # память от другой модели голосов не сравниваем
            ]
        return sorted((s for s in scored if s[1] >= RECOGNIZE_AT), key=lambda s: -s[1])

    def match(self, vector: np.ndarray) -> tuple[str, float] | None:
        """Имя самого похожего знакомого голоса, если он достаточно похож."""
        ranked = self.rank(vector)
        return ranked[0] if ranked else None

    def remember(self, name: str, vector: np.ndarray) -> bool:
        """Запоминает голос под именем; если такое имя уже есть, отпечаток усредняется (память «учится»).
        False — отпечаток непригоден (пустой или испорченный), ничего не записано."""
        name = name.strip()
        vector = _unit(vector)
        if not name or not np.isfinite(vector).all() or not np.linalg.norm(vector):
            return False
        with self._lock:
            for voice in self.voices:
                if voice.name.casefold() == name.casefold() and voice.vector.size == vector.size:
                    voice.vector = _unit(voice.vector * voice.count + vector)
                    voice.count = min(voice.count + 1, 50)  # старые созвоны не должны застывать навсегда
                    return True
            if len(self.voices) >= MAX_VOICES:
                self.voices.pop(0)
            self.voices.append(KnownVoice(name, vector))
        return True

    def forget(self, name: str | None = None) -> int:
        """Забывает одного человека или всех. Возвращает, сколько голосов стёрто."""
        with self._lock:
            before = len(self.voices)
            self.voices = [] if name is None else [v for v in self.voices if v.name.casefold() != name.casefold()]
            if not self.voices:
                self.path.unlink(missing_ok=True)
            else:
                self.save()
            return before - len(self.voices)

    @property
    def names(self) -> list[str]:
        with self._lock:
            return [v.name for v in self.voices]


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm and np.isfinite(norm) else vector
