"""Настройки приложения: пути, параметры звука и модели, пользовательский конфиг."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))  # запущены из собранного .exe / .app


def _user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Govorilka"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Govorilka"


# В собранном приложении папка программы может быть только для чтения,
# поэтому модели и настройки лежат в папке пользователя. При запуске из исходников — рядом с кодом.
DATA_DIR = Path(os.environ["GOVORILKA_DATA_DIR"]) if "GOVORILKA_DATA_DIR" in os.environ else (
    _user_data_dir() if FROZEN else APP_DIR
)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
MODELS_DIR = DATA_DIR / "models"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_TRANSCRIPTS_DIR = Path.home() / "Documents" / "Govorilka" if FROZEN else APP_DIR / "transcripts"
AUDIOTAP_PATH = RESOURCE_DIR / "bin" / "govorilka-audiotap"  # помощник системного звука для macOS

# Модель распознавания работает на 16 кГц моно
SAMPLE_RATE = 16_000

# Модели GigaAM (Сбер) в ONNX. e2e-версии сразу ставят пунктуацию и заглавные буквы.
MODELS = {
    "gigaam-v3-e2e-rnnt": "GigaAM v3 RNNT — точнее (по умолчанию)",
    "gigaam-v3-e2e-ctc": "GigaAM v3 CTC — чуть быстрее",
}
DEFAULT_MODEL = "gigaam-v3-e2e-rnnt"

SPEAKER_ME = "Я"
SPEAKER_OTHERS = "Собеседник"
SPEAKER_FILE = "Спикер"

SOURCE_MIC = "mic"
SOURCE_SYSTEM = "system"
SOURCE_FILE = "file"

DEFAULT_PROMPT = (
    "В этом файле транскрипт рабочего созвона: {file}. "
    "Прочитай его и сделай: короткое резюме, принятые решения, "
    "задачи с ответственными и сроками, открытые вопросы, риски для тестирования. "
    "Если созвон ещё идёт, файл дописывается — перечитай его, когда я попрошу."
)


@dataclass(frozen=True)
class Settings:
    """Пользовательские настройки, лежат в settings.json рядом с приложением."""

    model: str = DEFAULT_MODEL
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR)
    claude_workdir: str = ""  # пусто — запускать Claude Code в папке транскриптов
    claude_prompt: str = DEFAULT_PROMPT
    capture_mic: bool = True
    capture_system: bool = True
    echo_filter: bool = True  # убирать из микрофона звук колонок
    split_speakers: bool = True  # различать собеседников по голосу: «Собеседник 1», «Собеседник 2»

    @property
    def transcripts_path(self) -> Path:
        return Path(self.transcripts_dir)

    @property
    def claude_path(self) -> Path:
        return Path(self.claude_workdir) if self.claude_workdir else self.transcripts_path


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Не прочитал %s, беру настройки по умолчанию: %s", path, exc)
        return Settings()
    known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
    settings = replace(Settings(), **known)
    if settings.model not in MODELS:
        settings = replace(settings, model=DEFAULT_MODEL)
    return settings


def save_settings(settings: Settings, path: Path = SETTINGS_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Не сохранил настройки в %s: %s", path, exc)
