"""Настройки приложения: пути, параметры звука и модели, пользовательский конфиг."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from govorilka.hotkeys import DEFAULT_HOTKEY

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
BUNDLE_ID = "io.github.fedintsevqq.govorilka"  # тот же, что в govorilka.spec: по нему macOS помнит разрешения
FROZEN = bool(getattr(sys, "frozen", False))  # запущены из собранного .exe / .app


def _user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Govorilka"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Govorilka"


def downloads_dir() -> Path:
    """Папка «Загрузки» пользователя. На Windows её часто переносят на другой диск, поэтому спрашиваем систему."""
    if sys.platform == "win32":
        try:
            import ctypes

            class _Guid(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

            folder_id = _Guid(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            path = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path)) == 0:  # type: ignore[attr-defined]
                try:
                    if path.value:
                        return Path(path.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(path)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — запасной путь ниже подойдёт в большинстве случаев
            log.exception("Не узнал папку Загрузки у системы")
    return Path.home() / "Downloads"


# В собранном приложении папка программы может быть только для чтения,
# поэтому модели и настройки лежат в папке пользователя. При запуске из исходников — рядом с кодом.
DATA_DIR = Path(os.environ["GOVORILKA_DATA_DIR"]) if "GOVORILKA_DATA_DIR" in os.environ else (
    _user_data_dir() if FROZEN else APP_DIR
)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
MODELS_DIR = DATA_DIR / "models"
SETTINGS_FILE = DATA_DIR / "settings.json"
# Транскрипты по умолчанию лежат в Загрузках, в своей папке, чтобы не мешаться с остальными файлами
DEFAULT_TRANSCRIPTS_DIR = downloads_dir() / "Govorilka"
OLD_DEFAULT_TRANSCRIPTS_DIRS = (Path.home() / "Documents" / "Govorilka", APP_DIR / "transcripts")  # до версии 0.2
SETTINGS_VERSION = 2
PACKAGE_DIR = RESOURCE_DIR / "govorilka" if FROZEN else Path(__file__).resolve().parent
QML_DIR = PACKAGE_DIR / "qml"  # окна интерфейса
ASSETS_DIR = PACKAGE_DIR / "assets"  # иконки
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
SOURCE_MARK = "mark"  # то же, что transcript.SOURCE_MARK
SOURCE_NOTE = "note"  # то же, что transcript.SOURCE_NOTE

OLD_DEFAULT_PROMPT = (  # запрос до 0.3: говорил про файл, а в веб-чат уходит не файл, а текст
    "В этом файле транскрипт рабочего созвона: {file}. "
    "Прочитай его и сделай: короткое резюме, принятые решения, "
    "задачи с ответственными и сроками, открытые вопросы, риски для тестирования. "
    "Если созвон ещё идёт, файл дописывается — перечитай его, когда я попрошу."
)
DEFAULT_PROMPT = (
    "Транскрипт рабочего созвона: {file}. "
    "Прочитай его и сделай: короткое резюме, принятые решения, "
    "задачи с ответственными и сроками, открытые вопросы, риски для тестирования."
)


@dataclass(frozen=True)
class Settings:
    """Пользовательские настройки, лежат в settings.json рядом с приложением."""

    model: str = DEFAULT_MODEL
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR)
    claude_workdir: str = ""  # пусто — запускать Claude Code в папке транскриптов
    claude_prompt: str = DEFAULT_PROMPT  # что попросить у нейросети ({file} — транскрипт)
    ai_target: str = "auto"  # куда отправлять разговор: см. govorilka/ai.py
    capture_mic: bool = True
    capture_system: bool = True
    echo_filter: bool = True  # убирать из микрофона звук колонок
    split_speakers: bool = True  # различать собеседников по голосу: «Собеседник 1», «Собеседник 2»
    hotkey_enabled: bool = True  # старт и стоп записи по комбинации клавиш, когда окно свёрнуто
    hotkey: str = DEFAULT_HOTKEY  # «ctrl+shift», «alt+cmd», «ctrl+alt+r»
    show_indicator: bool = True  # маленькая плашка «● Запись» поверх окон
    hotkey_action: str = "menu"  # menu — круговое меню у курсора, record — сразу старт/стоп записи
    reduce_motion: bool = False  # вместо движения — короткое растворение
    theme: str = "system"  # system / dark / light
    tour_done: bool = False  # инструкцию при первом запуске уже прошли или пропустили
    remember_voices: bool = False  # запоминать голоса знакомых и узнавать их (биометрия: по желанию, локально)
    settings_version: int = SETTINGS_VERSION

    @property
    def transcripts_path(self) -> Path:
        return Path(self.transcripts_dir)

    @property
    def claude_path(self) -> Path:
        return Path(self.claude_workdir) if self.claude_workdir else self.transcripts_path


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    """Читает настройки; битый или чужой файл не должен мешать запуску."""
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # utf-8-sig: Блокнот добавляет BOM
    except (OSError, ValueError) as exc:  # ValueError покрывает и битый JSON, и не-UTF-8
        log.warning("Не прочитал %s, беру настройки по умолчанию: %s", path, exc)
        return Settings()
    if not isinstance(data, dict):
        log.warning("В %s не объект настроек, беру настройки по умолчанию", path)
        return Settings()
    defaults = Settings()
    if _same_path(data.get("transcripts_dir"), OLD_DEFAULT_TRANSCRIPTS_DIRS) and data.get("settings_version", 1) < 2:
        data.pop("transcripts_dir")  # папку никто не выбирал, это был прежний вариант по умолчанию: берём «Загрузки»
    # берём только известные поля нужного типа: строка вместо флага не должна доезжать до окна
    known = {
        key: value
        for key, value in data.items()
        if key in Settings.__dataclass_fields__ and isinstance(value, type(getattr(defaults, key)))
    }
    settings = replace(defaults, **known)
    if settings.claude_prompt == OLD_DEFAULT_PROMPT:  # запрос никто не менял — берём новый, подходящий и для чатов
        settings = replace(settings, claude_prompt=DEFAULT_PROMPT)
    if settings.model not in MODELS:
        settings = replace(settings, model=DEFAULT_MODEL)
    return settings


def _same_path(value: object, candidates: tuple[Path, ...]) -> bool:
    if not isinstance(value, str):
        return False
    normalized = os.path.normcase(os.path.normpath(value))
    return any(normalized == os.path.normcase(os.path.normpath(str(c))) for c in candidates)


def save_settings(settings: Settings, path: Path = SETTINGS_FILE) -> None:
    from govorilka.transcript import write_atomic  # через временный файл: сбой посреди записи не портит настройки

    try:
        write_atomic(path, json.dumps(asdict(settings), ensure_ascii=False, indent=2))
    except OSError as exc:
        log.error("Не сохранил настройки в %s: %s", path, exc)
