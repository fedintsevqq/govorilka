"""Мост между контроллером и QML.

Контроллер шлёт события из фоновых потоков; здесь они превращаются в сигналы Qt и попадают
в поток интерфейса. QML видит только свойства и слоты этого объекта.
"""

from __future__ import annotations

import functools
import gc
import re
import logging
import shutil
import math
import os
import sys
import threading
import time
from dataclasses import asdict, fields, replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import Property, QMimeData, QObject, QPoint, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QWindow

from govorilka.audio_base import PermissionDeniedError
from govorilka.config import MODELS_DIR, SAMPLE_RATE, SOURCE_MIC, SOURCE_SYSTEM, Settings
from govorilka.controller import AppState, ClaudeResult, Controller, Listener
from govorilka.engines import LOADING
from govorilka.recognizer import Recognizer
from govorilka.hotkeys import (
    ChordRecorder,
    Keyboard,
    chord_to_string,
    conflicts_with_language_switch,
    format_chord,
    format_snapshot,
    input_monitoring_status,
    parse_chord,
    system_keyboard,
    windows_language_switch,
)
from govorilka.pipeline import TextEvent
from govorilka import ai, asr_models, assistant, features, installer, model_catalog, permissions, secrets_store, updates, version
from govorilka import models as model_files  # имя models занято свойством со списком моделей
from govorilka import summary
from govorilka.qt import autopaste, capture_guard, mac_overlay
from govorilka.qt.foreground import allow_foreground_handoff, bring_to_front
from govorilka.qt.models import TranscriptModel
from govorilka.transcript import Transcript, format_offset

log = logging.getLogger(__name__)

CUSTOM_MODEL_ITEM = "__custom__"  # пункт «Своя модель…» в списке моделей: ключом не является

LEVEL_TICK_MS = 33  # индикаторы уровня — 30 кадров в секунду
LEVEL_DECAY = 0.86
ELAPSED_TICK_MS = 200
REFRESH_MS = 50  # реплики, пришедшие почти одновременно, рисуем одним обновлением
CAPTURE_TICK_MS = 40
HOTKEY_TICK_MS = 50  # 20 раз в секунду, как и фоновый опрос на Windows
CAPTURE_TIMEOUT_S = 10.0
PASTE_DELAY_MS = 60  # буфер обмена успевает обновиться до Ctrl+V
RESTORE_CLIPBOARD_MS = 600  # приложение успевает забрать текст из буфера, потом возвращаем прежний


def safe_slot(func):  # noqa: ANN001, ANN201
    """Слот QML, который не роняет молча: PySide6 при исключении только печатает трассу и идёт дальше,
    а человек видит «ничего не произошло». Здесь ошибка пишется в журнал и показывается сообщением."""

    @functools.wraps(func)
    def wrapper(self, *args):  # noqa: ANN001, ANN202
        try:
            return func(self, *args)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка в действии %s", func.__name__)
            self.toast.emit(f"Не получилось: {exc}", "error")
            return None

    return wrapper


def access_state(granted: bool | None) -> str:
    """Статус разрешения для QML: "yes" — разрешено, "no" — запрещено, "unknown" — macOS ещё не спрашивала."""
    return {True: "yes", False: "no"}.get(granted, "unknown")


def settings_to_map(settings: Settings) -> dict[str, object]:
    data = asdict(settings)
    for field_name, label in (("hotkey", "hotkeyLabel"), ("dictation_hotkey", "dictationLabel")):
        try:
            data[label] = format_chord(parse_chord(getattr(settings, field_name)))
        except ValueError:
            data[label] = getattr(settings, field_name)
    return data


def has_model_files(folder: Path) -> bool:
    """Модель распознавания лежит в папке: есть хоть один файл .onnx (недокачанные — с другим расширением)."""
    try:
        return folder.is_dir() and any(folder.rglob("*.onnx"))
    except OSError:
        return False


def copy_mime(source: QMimeData | None) -> QMimeData | None:
    """Копия содержимого буфера обмена во всех форматах: после диктовки вернём его на место."""
    if source is None or not source.formats():
        return None
    copy = QMimeData()
    for name in source.formats():
        copy.setData(name, source.data(name))
    return copy


CLEARABLE = {"claude_workdir"}  # пустое значение — осмысленное «по умолчанию» (папка транскриптов)


def map_to_settings(current: Settings, data: dict[str, object]) -> Settings:
    """Из словаря QML — в настройки. Неизвестные поля и значения не того типа пропускаем."""
    changes = {}
    for field in fields(Settings):
        if field.name not in data:
            continue
        value, default = data[field.name], getattr(current, field.name)
        if isinstance(default, bool):
            changes[field.name] = bool(value)
        elif isinstance(default, int):
            changes[field.name] = int(value)  # type: ignore[arg-type]
        elif isinstance(default, str):
            text = str(value).strip()
            if text or field.name in CLEARABLE:
                changes[field.name] = text
    return replace(current, **changes)


def sector_for(dx: float, dy: float, count: int, dead_zone: float) -> int:
    """Номер сектора кругового меню по смещению от центра. -1 — курсор в мёртвой зоне в центре.

    Сектор 0 смотрит строго вверх, дальше по часовой стрелке. Выбор идёт по направлению, а не по попаданию
    в значок: достаточно повести мышь в сторону нужного действия.
    """
    if count <= 0 or math.hypot(dx, dy) < dead_zone:
        return -1
    step = 360.0 / count
    angle = (math.degrees(math.atan2(dy, dx)) + 90.0 + step / 2) % 360.0  # 0° — вверх, по часовой
    return int(angle // step) % count


def to_path(url: str) -> Path:
    """QML отдаёт file:///… — превращаем в обычный путь."""
    qurl = QUrl(url)
    return Path(qurl.toLocalFile() if qurl.isLocalFile() else url)


class Backend(QObject):
    # ---------- из фоновых потоков (Qt сам перенесёт в поток окна) ----------
    _state_in = Signal(str)
    _status_in = Signal(str)
    _text_in = Signal(object)
    _level_in = Signal(str, float)
    _transcript_in = Signal(object, object)
    _progress_in = Signal(float)
    _error_in = Signal(str)
    _permission_in = Signal(object)
    _hotkey_in = Signal()
    _release_in = Signal()
    _dictation_in = Signal(str)
    _assistant_in = Signal(str, str)
    assistantChanged = Signal()  # noqa: N815
    _summary_status_in = Signal(str)
    _summary_done_in = Signal(str, str)
    _llm_progress_in = Signal(float)
    _llm_done_in = Signal(str, str)
    _llm_catalog_in = Signal()
    summaryChanged = Signal()  # noqa: N815
    summaryProgress = Signal()  # noqa: N815
    llmChanged = Signal()  # noqa: N815
    _model_progress_in = Signal(float)
    _model_done_in = Signal(str, str)
    catalogChanged = Signal()  # noqa: N815
    catalogProgress = Signal()  # noqa: N815
    _dictation_text_in = Signal(str)
    dictationChanged = Signal()  # noqa: N815
    dictationLevelChanged = Signal()  # noqa: N815
    capturedDictationHotkey = Signal(str, str, str)  # noqa: N815 — строка для настроек, подпись, предупреждение
    _finished_in = Signal(object, str)
    _ai_in = Signal(object)

    # ---------- для QML ----------
    stateChanged = Signal()  # noqa: N815
    statusChanged = Signal()  # noqa: N815
    elapsedChanged = Signal()  # noqa: N815
    levelsChanged = Signal()  # noqa: N815
    progressChanged = Signal()  # noqa: N815
    settingsChanged = Signal()  # noqa: N815
    transcriptInfoChanged = Signal()  # noqa: N815
    captureChanged = Signal()  # noqa: N815
    liveKeysChanged = Signal()  # noqa: N815
    toast = Signal(str, str)  # текст, вид: info / success / error
    permissionRequested = Signal(str, str, bool)  # noqa: N815 — текст, служба, можно ли сбросить
    radialRequested = Signal(int, int)  # noqa: N815 — где открыть круговое меню
    hotkeyReleased = Signal()  # noqa: N815
    windowRequested = Signal()  # noqa: N815
    settingsRequested = Signal()  # noqa: N815
    practiced = Signal(str)  # действие кругового меню, выбранное на тренировке в инструкции
    hiddenToTray = Signal()  # noqa: N815 — окно закрыли крестиком, приложение осталось в трее
    quitRequested = Signal()  # noqa: N815

    def __init__(self, controller_factory=None, parent: QObject | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._listener = _Relay(self)
        self._controller_factory = controller_factory or Controller
        self._model = TranscriptModel(self)
        self._transcript: Transcript | None = None
        self._partials: dict[str, TextEvent] = {}
        self._status = "Готово. Нажмите запись или откройте аудио/видео."
        self._elapsed = "00:00"
        self._levels = {SOURCE_MIC: 0.0, SOURCE_SYSTEM: 0.0}
        self._shown_levels = {SOURCE_MIC: 0.0, SOURCE_SYSTEM: 0.0}
        self._progress = 0.0
        self._file_name = ""
        self._live_keys = ""
        self._capturing = False
        self._tour_active = False
        self._ai_busy = False
        self._installing = False
        self._update: updates.UpdateInfo | None = None
        self._downloaded: dict[str, bool] = {}
        self._summary_busy = False
        self._summary_status = ""
        self._summary_fraction = -1.0
        self._summary_cancel = threading.Event()
        self._llm_download = ""
        self._llm_cancel = threading.Event()
        self._llm_fraction = 0.0
        self._ram_gb = summary.total_ram_gb()
        self._model_download = ""
        self._model_fraction = 0.0
        self._assist_phase = ""
        self._assist_question = ""
        self._assist_answer = ""
        self._assist_note = ""
        self._assist_hidden = False
        self._assist_protection = ""
        self._assist_key_saved: bool | None = None
        self._dictation_phase = ""
        self._dictation_note = ""
        self._dictation_level = 0.0
        self._capture_target = "hotkey"
        self._update_status = ""
        self._update_message = ""
        self._checking_model = False
        self._access_busy: set[str] = set()
        self._access_asked: set[str] = set()
        self._quitting = False
        self._loading = False
        self._load_fraction = -1.0
        self._key_watchers: set[str] = set()
        self._recorder: ChordRecorder | None = None
        self._capture_deadline = 0.0
        self._keyboard: Keyboard | None = None
        self._tray_available = False
        self._startup_notices: list[str] = []
        self._access_status: dict[str, bool | None] | None = None
        self._whats_new_since = ""
        self._install_error = ""

        for signal, slot in (
            (self._state_in, self._on_state),
            (self._status_in, self._set_status),
            (self._text_in, self._on_text),
            (self._level_in, self._on_level),
            (self._transcript_in, self._on_transcript),
            (self._progress_in, self._on_progress),
            (self._error_in, self._on_error),
            (self._permission_in, self._on_permission),
            (self._hotkey_in, self._on_hotkey),
            (self._release_in, self.hotkeyReleased),
            (self._finished_in, self._on_finished),
            (self._ai_in, self._on_ai_result),
            (self._access_done, self._on_access_done),
            (self._custom_in, self._on_custom_checked),
            (self._update_in, self._on_update),
            (self._install_in, self._on_install_done),
            (self._dictation_in, self._on_dictation),
            (self._assistant_in, self._on_assistant),
            (self._summary_status_in, self._on_summary_status),
            (self._summary_done_in, self._on_summary_done),
            (self._llm_progress_in, self._on_llm_progress),
            (self._llm_done_in, self._on_llm_done),
            (self._llm_catalog_in, self.llmChanged.emit),
            (self._model_progress_in, self._on_model_progress),
            (self._model_done_in, self._on_model_done),
            (self._dictation_text_in, self._on_dictation_text),
        ):
            signal.connect(slot)
        # контроллер создаём после подключения сигналов: его первое сообщение («горячая клавиша не включена») не пропадёт
        self.controller: Controller = self._controller_factory(self._listener)

        self._refresh_timer = _single_shot(self, REFRESH_MS, self._refresh)
        # таймеры подключаем через timeout.connect, а не аргументом конструктора: так PySide точно держит слот
        self._level_timer = _repeating(self, LEVEL_TICK_MS, self._tick_levels)
        self._level_timer.start()
        self._elapsed_timer = _repeating(self, ELAPSED_TICK_MS, self._tick_elapsed)
        self._capture_timer = _repeating(self, CAPTURE_TICK_MS, self._tick_capture)

    def start(self) -> None:
        self.controller.start()
        if getattr(self.controller.hotkeys, "needs_ticks", False):
            # Mac: горячую клавишу опрашиваем из главного потока — из фонового macOS прерывает программу
            self._hotkey_timer = _repeating(self, HOTKEY_TICK_MS, self._tick_hotkeys)
            self._hotkey_timer.start()
        self._startup_notices = self.controller.startup_notices()
        if self.controller.check_signing_change():
            self._startup_notices.append(
                "Говорилка теперь подписана по-новому, поэтому macOS попросит разрешения заново. Это один раз: "
                "они откроются в настройках, раздел «Разрешения macOS»."
            )
        self.controller.init_autostart()
        self._whats_new_since = self.controller.whats_new_since() or ""
        self._refresh_model_catalog()

    def _refresh_model_catalog(self) -> None:
        """Свежие адреса моделей итогов из репозитория: умершую ссылку правят там, без новой версии Говорилки."""
        model_catalog.load_cached()

        def work() -> None:
            if model_catalog.refresh():
                self._llm_catalog_in.emit()

        threading.Thread(target=work, name="model-catalog", daemon=True).start()

    def flush_startup_notices(self) -> None:
        """Сообщения при запуске (нет разрешения, старая версия рядом) показываем, когда окно уже готово."""
        for notice in self._startup_notices:
            self.toast.emit(notice, "info")
        self._startup_notices = []

    def shutdown(self) -> None:
        self.controller.shutdown()

    # ---------- свойства ----------

    def _get_state(self) -> str:
        return self.controller.state.value

    state = Property(str, _get_state, notify=stateChanged)

    def _get_status(self) -> str:
        return self._status

    statusText = Property(str, _get_status, notify=statusChanged)  # noqa: N815

    def _get_elapsed(self) -> str:
        return self._elapsed

    elapsed = Property(str, _get_elapsed, notify=elapsedChanged)

    micLevel = Property(float, lambda self: self._shown_levels[SOURCE_MIC], notify=levelsChanged)  # noqa: N815
    systemLevel = Property(float, lambda self: self._shown_levels[SOURCE_SYSTEM], notify=levelsChanged)  # noqa: N815
    fileProgress = Property(float, lambda self: self._progress, notify=progressChanged)  # noqa: N815
    fileName = Property(str, lambda self: self._file_name, notify=progressChanged)  # noqa: N815
    liveKeys = Property(str, lambda self: self._live_keys, notify=liveKeysChanged)  # noqa: N815
    capturing = Property(bool, lambda self: self._capturing, notify=captureChanged)
    isMac = Property(bool, lambda self: sys.platform == "darwin", constant=True)  # noqa: N815

    def _get_tray(self) -> bool:
        return self._tray_available

    trayAvailable = Property(bool, _get_tray, notify=settingsChanged)  # noqa: N815

    def set_tray_available(self, value: bool) -> None:
        self._tray_available = value
        self.settingsChanged.emit()

    @Slot()
    def noteHiddenToTray(self) -> None:  # noqa: N802
        self.hiddenToTray.emit()

    @Slot()
    def quit(self) -> None:
        self.quitRequested.emit()

    def _get_transcript_model(self) -> TranscriptModel:
        return self._model

    transcript = Property(QObject, _get_transcript_model, constant=True)

    def _get_transcript_path(self) -> str:
        return str(self.controller.last_file or "")

    transcriptPath = Property(str, _get_transcript_path, notify=transcriptInfoChanged)  # noqa: N815
    transcriptTitle = Property(  # noqa: N815
        str,
        lambda self: (self._transcript.title if self._transcript else "") or "",
        notify=transcriptInfoChanged,
    )

    def _get_settings(self) -> dict[str, object]:
        return settings_to_map(self.controller.settings)

    settings = Property("QVariantMap", _get_settings, notify=settingsChanged)

    def _get_models(self) -> list[dict[str, str]]:
        """Готовые модели и в конце пункт «Своя модель…», который открывает выбор своей."""
        return asr_models.catalog_entries() + [{"id": CUSTOM_MODEL_ITEM, "label": "Своя модель…"}]

    models = Property("QVariantList", _get_models, constant=True)

    # ---------- подсказки в звонке ----------

    assistantPhase = Property(str, lambda self: self._assist_phase, notify=assistantChanged)  # noqa: N815
    assistantQuestion = Property(str, lambda self: self._assist_question, notify=assistantChanged)  # noqa: N815
    assistantAnswer = Property(str, lambda self: summary.safe_markdown(self._assist_answer), notify=assistantChanged)  # noqa: N815
    assistantNote = Property(str, lambda self: self._assist_note, notify=assistantChanged)  # noqa: N815
    assistantHidden = Property(bool, lambda self: self._assist_hidden, notify=assistantChanged)  # noqa: N815
    assistantProtection = Property(str, lambda self: self._assist_protection, notify=assistantChanged)  # noqa: N815
    apiPresets = Property(  # noqa: N815
        "QVariantList",
        lambda self: [{"id": p.key, "label": p.title, "url": p.base_url, "model": p.model, "hint": p.hint} for p in assistant.API_PRESETS],
        constant=True,
    )

    def _on_assistant(self, kind: str, text: str) -> None:
        if kind == "off":
            self._assist_phase, self._assist_question, self._assist_answer, self._assist_note = "", "", "", ""
            self._assist_hidden = False
        elif kind == "status":
            self._assist_phase, self._assist_note = "loading", text
        elif kind == "ready":
            self._assist_phase, self._assist_note = "ready", "Слушаю вопросы собеседника"
        elif kind == "question":
            self._assist_phase, self._assist_question, self._assist_answer = "thinking", text, ""
        elif kind == "answer":
            self._assist_phase, self._assist_answer = "answering", text
        elif kind == "done":
            self._assist_phase, self._assist_answer = "ready", text or self._assist_answer
        elif kind == "error":
            self._assist_phase, self._assist_note = "error", text
        elif kind in ("check", "check-error"):
            self.toast.emit(text, "success" if kind == "check" else "error")
            return
        self.assistantChanged.emit()

    @Slot()
    @safe_slot
    def assistantAnswerLast(self) -> None:  # noqa: N802
        """Кнопка «Ответить»: на последнюю фразу собеседника, даже если она не похожа на вопрос."""
        if not self.controller.assistant_answer_last():
            self.toast.emit("Собеседник ещё ничего не сказал", "info")

    @Slot(bool)
    @safe_slot
    def setAssistantHidden(self, hidden: bool) -> None:  # noqa: N802
        self._assist_hidden = hidden
        self.assistantChanged.emit()

    @Slot()
    @safe_slot
    def copyAssistantAnswer(self) -> None:  # noqa: N802
        if self._assist_answer:
            QGuiApplication.clipboard().setText(self._assist_answer)

    @Slot(QWindow, bool)
    @safe_slot
    def protectWindow(self, window: QWindow, hidden: bool) -> None:  # noqa: N802
        """Прячет окно подсказок от демонстрации экрана (или возвращает как было)."""
        self._assist_protection = capture_guard.protect(int(window.winId()), hidden) if hidden else ""
        if not hidden:
            capture_guard.protect(int(window.winId()), False)
        self.assistantChanged.emit()

    def _key_saved(self) -> bool:
        if self._assist_key_saved is None:
            self._assist_key_saved = bool(secrets_store.get_secret(assistant.SECRET_NAME))
        return self._assist_key_saved

    assistantKeySaved = Property(bool, _key_saved, notify=assistantChanged)  # noqa: N815

    @Slot(str)
    @safe_slot
    def setAssistantKey(self, key: str) -> None:  # noqa: N802
        """Ключ API — в хранилище паролей системы; пустой удаляет."""
        try:
            secrets_store.set_secret(assistant.SECRET_NAME, key.strip())
        except secrets_store.SecretError as exc:
            self.toast.emit(str(exc), "error")
            return
        self._assist_key_saved = bool(key.strip())
        self.assistantChanged.emit()
        self.toast.emit("Ключ сохранён в хранилище паролей системы" if key.strip() else "Ключ удалён", "success")

    @Slot()
    @safe_slot
    def checkAssistantApi(self) -> None:  # noqa: N802
        """«Проверить подключение»: короткий запрос к провайдеру из настроек."""

        def work() -> None:
            try:
                endpoint = self.controller.assistant_endpoint()
                reply = assistant.stream_chat(endpoint, [{"role": "user", "content": "Ответь одним словом: работает?"}],
                                              lambda _t: None, max_tokens=8)
                self._assistant_in.emit("check", f"Подключение работает: {reply[:40] or 'ответ получен'}")
            except assistant.AssistantError as exc:
                self._assistant_in.emit("check-error", str(exc))

        threading.Thread(target=work, name="assistant-check", daemon=True).start()

    # ---------- возможности и «что нового» ----------

    featuresRequested = Signal(str)  # noqa: N815 — "new" (после обновления) или "all"

    def _feature_maps(self, items: list[features.Feature]) -> list[dict[str, object]]:
        mac = sys.platform == "darwin"
        s = self.controller.settings
        try:
            hotkey = format_chord(parse_chord(s.hotkey))
            dictation = format_chord(parse_chord(s.dictation_hotkey))
        except ValueError:
            hotkey, dictation = s.hotkey, s.dictation_hotkey
        return [features.render(f, hotkey, dictation, mac) for f in features.for_platform(items, mac)]

    featuresAll = Property("QVariantList", lambda self: self._feature_maps(features.FEATURES), notify=settingsChanged)  # noqa: N815
    featuresNew = Property(  # noqa: N815
        "QVariantList",
        lambda self: self._feature_maps(features.new_since(self._whats_new_since or features.BASELINE)),
        notify=settingsChanged,
    )
    guideVersion = Property(str, lambda self: features.guide_version(), constant=True)  # noqa: N815
    whatsNewPending = Property(bool, lambda self: bool(self._whats_new_since), constant=True)  # noqa: N815

    @Slot(str)
    @safe_slot
    def showFeatures(self, mode: str) -> None:  # noqa: N802
        self.featuresRequested.emit(mode)

    tourRequested = Signal()  # noqa: N815

    @Slot()
    @safe_slot
    def showTour(self) -> None:  # noqa: N802
        self.tourRequested.emit()

    # ---------- итоги созвона на локальной модели ----------

    summaryText = Property(str, lambda self: summary.safe_markdown(self._transcript.summary) if self._transcript else "", notify=summaryChanged)  # noqa: N815
    summaryBusy = Property(bool, lambda self: self._summary_busy, notify=summaryChanged)  # noqa: N815
    summaryStatus = Property(str, lambda self: self._summary_status, notify=summaryProgress)  # noqa: N815
    summaryFraction = Property(float, lambda self: self._summary_fraction, notify=summaryProgress)  # noqa: N815
    ramGb = Property(int, lambda self: round(self._ram_gb), constant=True)  # noqa: N815

    def _llm_map(self, model: summary.LlmModel) -> dict[str, object]:
        verdict, note = summary.fit(model, self._ram_gb)
        return {
            "key": model.key, "title": model.title, "blurb": model.blurb, "sizeMb": model.size_mb,
            "license": model.license, "recommended": model.recommended, "downloaded": summary.is_downloaded(model),
            "fit": verdict, "note": note, "minRam": model.min_ram_gb,
        }

    llmCatalog = Property("QVariantList", lambda self: [self._llm_map(m) for m in summary.CATALOG], notify=llmChanged)  # noqa: N815
    llmDownloading = Property(str, lambda self: self._llm_download, notify=llmChanged)  # noqa: N815
    llmDownloadFraction = Property(float, lambda self: self._llm_fraction, notify=summaryProgress)  # noqa: N815
    summaryModel = Property("QVariantMap", lambda self: self._llm_map(self.controller.summary_model()), notify=llmChanged)  # noqa: N815

    @Slot()
    @safe_slot
    def makeSummary(self) -> None:  # noqa: N802
        """«Сделать итоги»: при первом разе скачивает движок и модель, потом считает на этом компьютере."""
        if self._summary_busy:
            return
        self._summary_busy = True
        self._summary_cancel.clear()
        self._on_summary_status("Готовлю модель итогов…")
        self.summaryChanged.emit()

        def work() -> None:
            try:
                self.controller.make_summary(self._summary_status_in.emit, self._summary_cancel.is_set)
                self._summary_done_in.emit("ok", "")
            except summary.SummaryError as exc:
                self._summary_done_in.emit("error", str(exc))
            except Exception as exc:  # noqa: BLE001 — любая причина должна дойти до человека
                log.exception("Итоги не получились")
                self._summary_done_in.emit("error", f"Итоги не получились: {exc}")

        threading.Thread(target=work, name="summary", daemon=True).start()

    @Slot()
    @safe_slot
    def cancelSummary(self) -> None:  # noqa: N802
        self._summary_cancel.set()

    def _on_summary_status(self, text: str) -> None:
        self._summary_status = text
        match = re.search(r"\((\d+)%\)", text)
        self._summary_fraction = int(match.group(1)) / 100 if match else -1.0
        self.summaryProgress.emit()

    def _on_summary_done(self, result: str, message: str) -> None:
        self._summary_busy = False
        self._summary_status = ""
        self.summaryProgress.emit()
        self.summaryChanged.emit()
        self.llmChanged.emit()  # модель могла скачаться
        if result == "ok":
            self.toast.emit("Итоги готовы и сохранены в файл транскрипта", "success")
        elif message != "Отменено":
            self.toast.emit(message, "error")

    @Slot()
    @safe_slot
    def copySummary(self) -> None:  # noqa: N802
        if self._transcript and self._transcript.summary:
            QGuiApplication.clipboard().setText(self._transcript.summary)
            self.toast.emit("Итоги скопированы", "success")

    @Slot(str)
    @safe_slot
    def downloadLlm(self, key: str) -> None:  # noqa: N802
        model = summary.CATALOG_BY_KEY.get(key)
        if model is None or self._llm_download:
            return
        self._llm_download, self._llm_fraction = key, 0.0
        self._llm_cancel.clear()
        self.llmChanged.emit()

        def progress(text: str) -> None:
            match = re.search(r"\((\d+)%\)", text)
            if match:
                self._llm_progress_in.emit(int(match.group(1)) / 100)

        def work() -> None:
            try:
                summary.ensure_engine(progress, self._llm_cancel.is_set)
                summary.ensure_model(model, progress, self._llm_cancel.is_set)
                self._llm_done_in.emit(key, "")
            except Exception as exc:  # noqa: BLE001
                log.warning("Модель итогов %s не скачалась: %s", key, exc)
                self._llm_done_in.emit(key, str(exc))

        threading.Thread(target=work, name="llm-download", daemon=True).start()

    @Slot()
    @safe_slot
    def cancelLlmDownload(self) -> None:  # noqa: N802
        self._llm_cancel.set()

    def _on_llm_progress(self, fraction: float) -> None:
        self._llm_fraction = fraction
        self.summaryProgress.emit()

    def _on_llm_done(self, key: str, error: str) -> None:
        self._llm_download = ""
        title = summary.CATALOG_BY_KEY[key].title
        if error == "Отменено":
            self.toast.emit(f"Загрузка {title} отменена. Скачанное сохранено: следующая продолжится с того же места", "info")
        elif error:
            self.toast.emit(f"{title} не скачалась: {error}", "error")
        else:
            self.toast.emit(f"{title} скачана: итоги будут работать без интернета", "success")
        self.llmChanged.emit()

    @Slot(str)
    @safe_slot
    def deleteLlm(self, key: str) -> None:  # noqa: N802
        model = summary.CATALOG_BY_KEY.get(key)
        if model is None:
            return
        if self._llm_download == key or self._summary_busy or (self._assist_phase and self._assist_phase != "error"):
            # модель качается или работает: удалённый на ходу файл сломал бы итоги или подсказки
            self.toast.emit(f"{model.title} сейчас занята: дождитесь конца загрузки, итогов или записи с подсказками", "info")
            return
        try:
            freed = summary.delete_model(model)
        except OSError as exc:
            self.toast.emit(f"Не получилось удалить {model.title}: файл занят другой программой ({exc.strerror})", "error")
            return
        self.llmChanged.emit()
        self.toast.emit(f"{model.title} удалена" + (f": освобождено {freed} МБ" if freed else ""), "success")

    # ---------- каталог моделей распознавания ----------

    def _catalog_maps(self) -> list[dict[str, object]]:
        return [
            {
                "key": m.key, "title": m.title, "blurb": m.blurb, "languages": m.languages, "sizeMb": m.size_mb,
                "accuracy": m.accuracy, "speed": m.speed, "punctuation": m.punctuation, "recommended": m.recommended,
                "downloaded": self._downloaded.get(m.key, False),
            }
            for m in asr_models.CATALOG
        ]

    catalog = Property("QVariantList", _catalog_maps, notify=catalogChanged)
    modelDownloading = Property(str, lambda self: self._model_download, notify=catalogChanged)  # noqa: N815
    modelDownloadFraction = Property(float, lambda self: self._model_fraction, notify=catalogProgress)  # noqa: N815

    @Slot()
    @safe_slot
    def refreshCatalog(self) -> None:  # noqa: N802
        """Какие модели уже скачаны: смотрим, есть ли в папке модели файлы ONNX, когда открывают настройки.
        Быстро и в потоке окна: фоновый поток мог бы прислать ответ уже закрытому окну."""
        # «скачана» — только если на месте все файлы модели: первый докачанный .onnx из трёх ещё не модель
        self._on_catalog({m.key: has_model_files(asr_models.parse(m.key).local_dir) and model_files.is_downloaded("asr", m.key)
                          for m in asr_models.CATALOG})

    def _on_catalog(self, found: dict) -> None:
        self._downloaded = dict(found)
        self.catalogChanged.emit()

    @Slot(str)
    @safe_slot
    def downloadModel(self, key: str) -> None:  # noqa: N802
        """«Скачать» у модели в каталоге: качаем заранее, чтобы первая запись не ждала."""
        entry = asr_models.CATALOG_BY_KEY.get(key)
        if entry is None or self._model_download:
            return
        self._model_download, self._model_fraction = key, 0.0
        self.catalogChanged.emit()

        def progress(text: str) -> None:
            match = re.search(r"\((\d+)%\)", text)
            if match:
                self._model_progress_in.emit(int(match.group(1)) / 100)

        def work() -> None:
            try:
                model_files.ensure_downloaded("asr", key, entry.title, progress)
                self._model_done_in.emit(key, "")
            except Exception as exc:  # noqa: BLE001 — нет сети, не хватило места: человек должен узнать
                log.warning("Модель %s не скачалась: %s", key, exc)
                self._model_done_in.emit(key, str(exc))

        threading.Thread(target=work, name="model-download", daemon=True).start()

    def _on_model_progress(self, fraction: float) -> None:
        self._model_fraction = fraction
        self.catalogProgress.emit()

    def _on_model_done(self, key: str, error: str) -> None:
        self._model_download = ""
        title = asr_models.CATALOG_BY_KEY[key].title
        if error:
            self.toast.emit(f"{title} не скачалась: {error}. Проверьте интернет и попробуйте ещё раз", "error")
        else:
            self._downloaded[key] = True
            self.toast.emit(f"{title} скачана: можно выбрать её и сохранить настройки", "success")
        self.catalogChanged.emit()

    @Slot(str)
    @safe_slot
    def deleteModel(self, key: str) -> None:  # noqa: N802
        """Корзина у скачанной модели: освобождаем место на диске."""
        entry = asr_models.CATALOG_BY_KEY.get(key)
        if entry is None:
            return
        if key == self.controller.settings.model:
            self.toast.emit("Эта модель сейчас выбрана. Сначала выберите другую и сохраните настройки", "info")
            return
        folder = asr_models.parse(key).local_dir
        freed = model_files.folder_bytes(folder) // model_files.MB if folder.exists() else 0
        shutil.rmtree(folder, ignore_errors=True)
        self._downloaded[key] = False
        self.catalogChanged.emit()
        self.toast.emit(f"{entry.title} удалена" + (f": освобождено {freed} МБ" if freed else ""), "success")
    customTypes = Property(  # noqa: N815
        "QVariantList",
        lambda self: [{"id": key, "label": label, "files": files} for key, label, files in asr_models.CUSTOM_TYPES],
        constant=True,
    )

    @Slot(str, result=str)
    def modelLabel(self, key: str) -> str:  # noqa: N802
        return asr_models.display_name(key)

    @Slot(str, result=bool)
    def isCustomModel(self, key: str) -> bool:  # noqa: N802
        return asr_models.is_custom(key)

    customModelChecked = Signal(bool, str, str)  # noqa: N815 — удалось, сообщение, ключ модели
    _custom_in = Signal(bool, str, str)
    _update_in = Signal(object, str, bool)
    _install_in = Signal(bool, str)

    @Slot(str, str, str, bool)
    @safe_slot
    def checkCustomModel(self, kind: str, reference: str, model_type: str, int8: bool) -> None:  # noqa: N802
        """Проверяет свою модель: находит или скачивает файлы, загружает и прогоняет через неё тишину. Идёт в фоне:
        скачивание бывает долгим, а прогресс виден в кольце загрузки."""
        if self._checking_model:
            return
        self._checking_model = True
        reference = reference.strip().strip('"')

        def work() -> None:
            ok, message, key = False, "", ""
            recognizer = None
            try:
                if kind == "hf":
                    key = asr_models.hf_key(reference, asr_models.INT8 if int8 else None)
                else:
                    key = asr_models.dir_key(model_type or asr_models.AUTO_TYPE, reference)
                ref = asr_models.parse(key)
                recognizer = Recognizer(key, self._status_in.emit)
                recognizer.transcribe(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))  # модель должна не просто загрузиться, но и отработать
                ok, message = True, f"Модель работает: {ref.label}. Нажмите «Сохранить», и она станет основной."
            except asr_models.ModelError as exc:
                message = str(exc)
            except Exception as exc:  # noqa: BLE001 — любая причина должна дойти до человека, а не оборвать проверку
                log.exception("Своя модель не прошла проверку")
                message = str(exc)
            finally:
                del recognizer  # веса освободятся сразу; сборка мусора — в главном потоке (см. _on_custom_checked)
            self._custom_in.emit(ok, message, key if ok else "")

        threading.Thread(target=work, name="check-custom-model", daemon=True).start()

    # ---------- обновления ----------

    updateChanged = Signal()  # noqa: N815
    appVersion = Property(str, lambda self: version.VERSION, constant=True)  # noqa: N815
    updateInfo = Property("QVariantMap", lambda self: self._update_map(), notify=updateChanged)  # noqa: N815
    updateStatus = Property(str, lambda self: self._update_status, notify=updateChanged)  # noqa: N815 — "", checking, latest, error, available
    updateMessage = Property(str, lambda self: self._update_message, notify=updateChanged)  # noqa: N815

    def _update_map(self) -> dict[str, object]:
        info = self._update
        if info is None:
            return {"available": False}
        return {
            "available": True,
            "version": info.version,
            "notes": info.notes,
            "page": info.page_url,
            "hasDownload": bool(info.asset_url),
            "sizeMb": round(info.size / 1_048_576) if info.size else 0,
        }

    def _set_update_state(self, status: str, message: str = "") -> None:
        self._update_status, self._update_message = status, message
        self.updateChanged.emit()

    @Slot()
    @safe_slot
    def checkForUpdates(self) -> None:  # noqa: N802
        """Кнопка «Проверить обновление»."""
        if self._update_status == "checking":
            return
        self._set_update_state("checking", "Проверяю…")
        self.controller.check_for_update(manual=True)

    def _on_update(self, info: object | None, error: str, manual: bool) -> None:
        self._update = info  # type: ignore[assignment]
        if info is not None:
            self._set_update_state("available", f"Доступна версия {info.version}")  # type: ignore[attr-defined]
        elif error:
            if manual:
                self._set_update_state("error", f"Не получилось проверить: {error}. Проверьте интернет и попробуйте ещё раз")
            else:
                self._set_update_state("")
        else:
            self._set_update_state("latest" if manual else "", f"У вас последняя версия: {version.VERSION}" if manual else "")
        if manual and not error and info is None:
            self.toast.emit(f"У вас последняя версия: {version.VERSION}", "success")

    canInstallUpdate = Property(bool, lambda self: installer.can_install(), constant=True)  # noqa: N815
    installError = Property(str, lambda self: self._install_error, notify=updateChanged)  # noqa: N815
    installHint = Property(str, lambda self: self._install_hint(), constant=True)  # noqa: N815
    isTrialBuild = Property(bool, lambda self: version.is_trial_build(), constant=True)  # noqa: N815

    @staticmethod
    def _install_hint() -> str:
        """Почему «Установить» недоступно и что сделать (пусто — доступно или это не Mac)."""
        if installer.can_install() or sys.platform != "darwin" or not installer.FROZEN:
            return ""
        return installer.mac_location_problem(installer.app_bundle())
    installing = Property(bool, lambda self: self._installing, notify=updateChanged)

    @Slot()
    @safe_slot
    def installUpdate(self) -> None:  # noqa: N802
        """«Установить»: скачать новый файл, проверить его и заменить им программу. Говорилка закроется и откроется заново."""
        info = self._update
        if info is None or self._installing:
            return
        if not installer.can_install():
            hint = self._install_hint()
            if hint:  # Mac: запущена из образа или из папки без записи — молча открывать браузер нельзя, человек не поймёт
                self._install_error = hint
                self.updateChanged.emit()
                self.toast.emit(hint, "error")
                return
            self.downloadUpdate()  # из исходников: скачивание браузером, как раньше
            return
        if self.controller.state != AppState.IDLE or self.controller.busy:
            self.toast.emit("Дождитесь конца записи или расшифровки: после установки Говорилка перезапустится", "info")
            return
        self._installing = True
        self._install_error = ""
        self.updateChanged.emit()

        def work() -> None:
            try:
                installer.install(info, self._status_in.emit)
                self._install_in.emit(True, "")
            except installer.InstallError as exc:
                log.warning("Обновление не установлено: %s", exc)
                self._install_in.emit(False, str(exc))
            except Exception as exc:  # noqa: BLE001 — любая причина должна дойти до человека
                log.exception("Сбой установки обновления")
                self._install_in.emit(False, f"Не получилось установить: {exc}")

        threading.Thread(target=work, name="install-update", daemon=True).start()

    def _on_install_done(self, ok: bool, message: str) -> None:
        if ok:
            self._set_status("Устанавливаю обновление: Говорилка сейчас перезапустится…")
            self.toast.emit("Обновление скачано и проверено. Говорилка перезапустится сама", "success")
            QTimer.singleShot(700, self.quit)
            return
        self._installing = False
        self._install_error = message
        self._set_status("Готово")
        self.updateChanged.emit()
        self.toast.emit(message, "error")

    @Slot()
    @safe_slot
    def downloadUpdate(self) -> None:  # noqa: N802
        """Скачивание файла новой версии браузером (в «Загрузки»); ссылка идёт на github.com."""
        info = self._update
        if info is None:
            return
        QDesktopServices.openUrl(QUrl(info.asset_url or info.page_url))
        self.toast.emit("Скачиваю новую версию. Закройте Говорилку и запустите скачанный файл", "info")

    @Slot()
    @safe_slot
    def openReleasePage(self) -> None:  # noqa: N802
        if self._update is not None:
            QDesktopServices.openUrl(QUrl(self._update.page_url))

    @Slot()
    @safe_slot
    def skipUpdate(self) -> None:  # noqa: N802
        """«Пропустить эту версию»: пока не выйдет следующая, не предлагаем."""
        if self._update is None:
            return
        self.controller.update_settings(replace(self.controller.settings, skipped_update=self._update.version))
        self._update = None
        self._set_update_state("")
        self.settingsChanged.emit()

    @Slot()
    @safe_slot
    def dismissUpdate(self) -> None:  # noqa: N802
        """«Позже»: плашка прячется до следующего запуска."""
        self._update = None
        self._set_update_state("")

    def _on_custom_checked(self, ok: bool, message: str, key: str) -> None:
        self._checking_model = False
        self._status_in.emit("Готово")
        self.customModelChecked.emit(ok, message, key)

    def _get_ai(self) -> dict[str, str]:
        target = ai.TARGETS[self.controller.ai_target()]
        return {"name": target.name, "short": target.short, "web": bool(target.url), "id": self.controller.ai_target()}

    aiTarget = Property("QVariantMap", _get_ai, notify=settingsChanged)  # noqa: N815 — куда уйдёт разговор
    aiOpenChoices = Property(  # noqa: N815
        "QVariantList",
        lambda self: [
            {"id": "auto", "label": "В приложении, если оно установлено, иначе в браузере"},
            {"id": "browser", "label": "Всегда в браузере"},
        ],
        constant=True,
    )
    aiChoices = Property(  # noqa: N815
        "QVariantList",
        lambda self: [
            {"id": key, "label": label, "short": ai.TARGETS[key].short, "vpn": bool(ai.TARGETS[key].probe)}
            for key, label in ai.CHOICES
        ],
        constant=True,
    )

    autostart = Property(bool, lambda self: self.controller.autostart_enabled(), notify=settingsChanged)
    inputMonitoringMissing = Property(  # noqa: N815
        bool, lambda self: input_monitoring_status() is False, notify=settingsChanged
    )

    # ---------- действия из QML ----------

    @Slot(str)
    @safe_slot
    def toggleRecording(self, title: str = "") -> None:  # noqa: N802
        if self.controller.state == AppState.STARTING:
            self.toast.emit("Подождите, запись запускается: при первом запуске загружается модель", "info")
            return
        self.controller.toggle_recording(title)

    @Slot(str)
    @safe_slot
    def startRecording(self, title: str = "") -> None:  # noqa: N802
        """Только начать (не переключать): кнопка «Начать запись» не должна останавливать уже идущую."""
        if self.controller.state == AppState.IDLE:
            self.controller.start_recording(title)

    @Slot()
    @safe_slot
    def togglePause(self) -> None:  # noqa: N802
        if self.controller.session is None:
            self.toast.emit("Пауза работает во время записи", "info")
            return
        self.controller.toggle_pause()

    @Slot()
    @safe_slot
    def stopRecording(self) -> None:  # noqa: N802
        self.controller.stop_recording()

    @Slot()
    @safe_slot
    def addMark(self) -> None:  # noqa: N802
        if not self.controller.add_mark():
            self.toast.emit("Отметка ставится во время записи: она помечает важный момент разговора", "info")
            return
        self.toast.emit(f"★ Момент {self._elapsed} отмечен — звёздочка есть в тексте и в файле", "success")

    @Slot(str)
    @safe_slot
    def openFile(self, url: str) -> None:  # noqa: N802
        if url and not self.controller.open_file(to_path(url)):
            self.toast.emit("Сначала остановите запись", "info")

    @Slot()
    @safe_slot
    def cancelFile(self) -> None:  # noqa: N802
        self.controller.cancel_file()

    @Slot()
    @safe_slot
    def sendToAi(self) -> None:  # noqa: N802
        """Проверка доступности зарубежного чата ходит в сеть (до 4 с): делаем её в фоне, окно не замирает."""
        if self._ai_busy:
            return
        self._ai_busy = True
        if ai.TARGETS[self.controller.ai_target()].probe:
            self.toast.emit(f"Проверяю, доступен ли {self.aiTarget['name']}…", "info")

        def work() -> None:
            try:
                result = self.controller.send_to_ai()
            except Exception as exc:  # noqa: BLE001 — сбой не должен оставить кнопку «занятой» навсегда
                log.exception("Не отправил разговор нейросети")
                result = ClaudeResult(False, f"Не получилось отправить: {exc}")
            self._ai_in.emit(result)

        threading.Thread(target=work, name="send-to-ai", daemon=True).start()

    def _on_ai_result(self, result: ClaudeResult) -> None:
        self._ai_busy = False
        if result.clipboard:
            QGuiApplication.clipboard().setText(result.clipboard)
        if result.url:
            allow_foreground_handoff()  # браузер должен выйти вперёд, иначе вставлять будет некуда
            QDesktopServices.openUrl(QUrl(result.url))
        if result.target:
            autopaste.start(result.target, on_no_permission=lambda: self.toast.emit(
                "macOS не дал вставить текст: включите Говорилку в Настройки → Конфиденциальность и безопасность → "
                "Универсальный доступ. Пока — нажмите ⌘V в чате, разговор в буфере", "error"))
        self.toast.emit(result.message, "success" if result.ok else "error")

    def _storage(self, kind: str) -> str:
        """Где лежат модели и сколько места занято: «~/Library/…/models · 2,3 ГБ»."""
        folder = MODELS_DIR if kind == "asr" else summary.LLM_DIR
        used = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0
        shown = str(folder)
        home = str(Path.home())
        if shown.startswith(home):
            shown = "~" + shown[len(home):]
        size = f"{used / 1024**3:.1f} ГБ".replace(".", ",") if used >= 1024**3 else f"{used // 1024**2} МБ"
        return f"{shown} · занято {size}"

    asrStorage = Property(str, lambda self: self._storage("asr"), notify=catalogChanged)  # noqa: N815
    llmStorage = Property(str, lambda self: self._storage("llm"), notify=llmChanged)  # noqa: N815

    @Slot(str)
    @safe_slot
    def openStorage(self, kind: str) -> None:  # noqa: N802
        """«Открыть папку» у моделей: Finder или Проводник на папке, куда они скачиваются."""
        folder = MODELS_DIR if kind == "asr" else summary.LLM_DIR
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @Slot()
    @safe_slot
    def openFolder(self) -> None:  # noqa: N802
        self.controller.open_folder()

    @Slot()
    @safe_slot
    def openTranscript(self) -> None:  # noqa: N802
        if not self.controller.open_transcript():
            self.toast.emit("Транскрипта пока нет", "info")

    @Slot(int, int, int, int)
    @safe_slot
    def copySelection(self, start_row: int, start_pos: int, end_row: int, end_pos: int) -> None:  # noqa: N802
        """Ctrl+C (⌘C) или «Копировать» в ленте: выделенный мышью текст в буфер обмена."""
        text = self._model.selection_text(start_row, start_pos, end_row, end_pos)
        if text:
            QGuiApplication.clipboard().setText(text)

    @Slot()
    @safe_slot
    def copyTranscript(self) -> None:  # noqa: N802
        text = self.controller.plain_text()
        if not text:
            self.toast.emit("Пока нечего копировать", "info")
            return
        QGuiApplication.clipboard().setText(text)
        self.toast.emit("Транскрипт скопирован", "success")

    @Slot(str)
    @safe_slot
    def saveAs(self, url: str) -> None:  # noqa: N802
        try:
            self.toast.emit(self.controller.save_as(to_path(url)), "success")
        except (OSError, ValueError) as exc:
            self.toast.emit(f"Не сохранил: {exc}", "error")

    @Slot("QVariantMap", bool)
    @safe_slot
    def applySettings(self, data: dict[str, object], autostart_on: bool) -> None:  # noqa: N802
        settings = map_to_settings(self.controller.settings, data)
        problems = self.controller.update_settings(settings, autostart_on)
        self.settingsChanged.emit()
        for problem in problems:
            self.toast.emit(problem, "error")
        if not problems:
            self.toast.emit("Настройки сохранены", "success")

    @Slot(str, "QVariant")
    @safe_slot
    def setOption(self, name: str, value: object) -> None:  # noqa: N802
        """Быстрые переключатели в главном окне: микрофон, звук компьютера, эхо, голоса."""
        settings = map_to_settings(self.controller.settings, {name: value})
        self.controller.update_settings(settings)
        self.settingsChanged.emit()

    @Slot(str)
    @safe_slot
    def resetPermission(self, service: str) -> None:  # noqa: N802
        failure = self.controller.reset_permission(service)
        if failure:
            self.toast.emit(f"Не получилось сбросить разрешение: {failure}", "error")
            return
        self.toast.emit("Готово. Откройте Говорилку заново и разрешите доступ.", "success")
        QTimer.singleShot(1500, QGuiApplication.quit)

    @Slot(int, str)
    @safe_slot
    def editMark(self, row: int, text: str) -> None:  # noqa: N802
        """Подписать отметку своими словами. Пустой текст возвращает прежнюю подпись «Отметка»."""
        item = self._mark_at(row)
        if item is None:
            return
        if self.controller.edit_mark(item, text.strip() or "Отметка"):
            self._refresh()  # сразу: следующая правка должна видеть уже новую строку, а не прежнюю
            self.toast.emit("Отметка обновлена, в файле тоже", "success")

    @Slot(int)
    @safe_slot
    def deleteMark(self, row: int) -> None:  # noqa: N802
        item = self._mark_at(row)
        if item is not None and self.controller.edit_mark(item, None):
            self._refresh()
            self.toast.emit("Отметка убрана", "success")

    def _mark_at(self, row: int) -> object | None:
        rows = self._model.rows
        if not 0 <= row < len(rows) or rows[row].kind != "mark":
            return None
        return rows[row].key

    @Slot(str, str)
    @safe_slot
    def renameSpeaker(self, voice_id: str, name: str) -> None:  # noqa: N802
        """Подписать говорящего именем: во всех его репликах и, если включена память голосов, навсегда."""
        if not name.strip():
            return
        changed = self.controller.rename_speaker(voice_id, name)
        self._schedule_refresh()
        self.settingsChanged.emit()
        if changed:
            self.toast.emit(f"«{name.strip()}»: подписано реплик — {changed}", "success")

    @Slot()
    @safe_slot
    def forgetVoices(self) -> None:  # noqa: N802
        count = self.controller.forget_voices()
        self.settingsChanged.emit()
        self.toast.emit(f"Забыл голосов: {count}" if count else "Память голосов и так пуста", "success" if count else "info")

    knownVoices = Property(int, lambda self: len(self.controller.known_voices()), notify=settingsChanged)  # noqa: N815

    @Slot(bool)
    @safe_slot
    def setTourActive(self, on: bool) -> None:  # noqa: N802
        """Тренировка в инструкции: клавиша всегда открывает меню, а выбор в нём ничего не запускает."""
        self._tour_active = on

    @Slot()
    @safe_slot
    def finishTour(self) -> None:  # noqa: N802
        self._tour_active = False
        if not self.controller.settings.tour_done:
            self.controller.update_settings(map_to_settings(self.controller.settings, {"tour_done": True}))
            self.settingsChanged.emit()

    accessChanged = Signal()  # noqa: N815

    def _get_access(self) -> list[dict[str, object]]:
        if sys.platform != "darwin":
            return []
        if self._access_status is None:  # спрашиваем macOS один раз на обновление, а не на каждую привязку в окне
            self._access_status = permissions.access_status()
        status = self._access_status
        # Статус строкой, а не True/False/None: None внутри списка для QML PySide превращает в обёртку Python-объекта,
        # и на macOS чтение такой строки из QML портило память — окно настроек падало через пару секунд
        return [
            {"id": key, "pane": pane, "label": label, "hint": hint, "state": access_state(status.get(key))}
            for key, pane, label, hint in permissions.ACCESS_ROWS
        ]

    access = Property("QVariantList", _get_access, notify=accessChanged)
    accessAvailable = Property(bool, lambda self: sys.platform == "darwin", constant=True)  # noqa: N815

    @Slot()
    @safe_slot
    def refreshAccess(self) -> None:  # noqa: N802
        """Таймер в окне: статус изменился — перерисовать строки. Не изменился — не трогать (строки не пересоздаются)."""
        if sys.platform != "darwin":
            return
        fresh = permissions.access_status()
        if fresh == self._access_status:
            return
        self._access_status = fresh
        self.accessChanged.emit()

    liveFlags = Property(str, lambda self: os.environ.get("GOVORILKA_LIVE_FLAGS", ""), constant=True)  # noqa: N815 — поиск сбоев в CI

    _access_done = Signal(str)

    @Slot(str)
    @safe_slot
    def requestAccess(self, key: str) -> None:  # noqa: N802
        """Кнопка «Разрешить»: показать системный запрос (если macOS ещё не спрашивала), затем, если доступа так и нет,
        открыть нужный раздел настроек. Запрос ждёт ответа человека, поэтому идёт в фоне."""
        if key not in {row[0] for row in permissions.ACCESS_ROWS} or key in self._access_busy:
            return  # неизвестная служба или запрос уже идёт (двойной щелчок)
        self._access_busy.add(key)
        first = key not in self._access_asked
        self._access_asked.add(key)

        token = key if first else f"open:{key}"
        if key not in ("microphone", "automation"):
            # Запросы экрана, клавиатуры и универсального доступа — вызовы macOS внутри программы: только из главного
            # потока (из фонового они шли одновременно с опросом клавиш и роняли программу). Они не ждут ответа.
            if first:
                permissions.request(key)
            QTimer.singleShot(0, lambda: self._on_access_done(token))
            return

        def work() -> None:  # микрофон и автоматизация ждут ответа человека: через отдельные программы, в фоне
            if first:
                permissions.request(key)
            self._access_done.emit(token)

        threading.Thread(target=work, name=f"access-{key}", daemon=True).start()

    def _on_access_done(self, token: str) -> None:
        key = token.removeprefix("open:")
        self._access_busy.discard(key)
        self._access_status = None  # человек мог только что выдать доступ: перечитать
        self.accessChanged.emit()
        status = permissions.access_status().get(key)
        # первый щелчок только показывает системный запрос (раздел настроек поверх него мешал бы ответить);
        # второй, или первый после отказа, открывает нужный раздел
        if status is not True and (token.startswith("open:") or status is False and key in ("microphone", "automation")):
            pane = next(row[1] for row in permissions.ACCESS_ROWS if row[0] == key)
            QDesktopServices.openUrl(QUrl(permissions.privacy_link(pane)))

    @Slot(str)
    @safe_slot
    def openPrivacy(self, pane: str) -> None:  # noqa: N802
        """Открывает нужный раздел «Конфиденциальность и безопасность»: человеку не надо искать его самому."""
        if pane in {p for _k, p, *_ in permissions.ACCESS_ROWS}:
            QDesktopServices.openUrl(QUrl(permissions.privacy_link(pane)))

    @Slot()
    @safe_slot
    def resetAllPermissions(self) -> None:  # noqa: N802
        """Для переустановки: macOS помнит прежнюю копию. Сбрасываем всё, закрываемся, при новом запуске система спросит заново."""
        if not permissions.can_reset():
            self.toast.emit("Сброс разрешений работает только в установленном приложении на Mac", "info")
            return
        problems = permissions.reset_all()
        if problems:
            self.toast.emit("Не всё получилось сбросить: " + "; ".join(problems), "error")
            return
        self.toast.emit("Разрешения сброшены, Говорилка закроется. Откройте её снова: система спросит о каждом доступе", "success")
        QTimer.singleShot(1800, self.quit)

    quittingChanged = Signal()  # noqa: N815
    quitting = Property(bool, lambda self: self._quitting, notify=quittingChanged)

    def begin_quit(self) -> None:
        """Cmd+Q и «Выйти» в Dock: закрытие окна в этот момент должно завершить программу, а не спрятать окно в трей."""
        self._quitting = True
        self.quittingChanged.emit()
        QTimer.singleShot(1500, self._end_quit)  # если выход кто-то отменил, флаг не должен залипнуть

    def _end_quit(self) -> None:
        self._quitting = False
        self.quittingChanged.emit()

    @Slot(bool)
    @safe_slot
    def setTrayMode(self, hidden: bool) -> None:  # noqa: N802
        """Окно спрятано в трей — на Mac это приложение строки меню без значка в Dock; окно показано — обычное."""
        if sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa":
            mac_overlay.set_activation_policy(accessory=hidden)

    @Slot(QObject)
    @safe_slot
    def prepareOverlay(self, window: QObject) -> None:  # noqa: N802
        """Круговое меню и плашка записи должны появляться над полноэкранными приложениями (macOS)."""
        # только на настоящей платформе macOS: в тестах (offscreen) winId() — не вид NSView, и обращение к нему упадёт
        if sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa" and isinstance(window, QWindow):
            mac_overlay.apply(int(window.winId()))

    @Slot(QObject)
    @safe_slot
    def bringToFront(self, window: QObject) -> None:  # noqa: N802
        if sys.platform == "win32" and isinstance(window, QWindow):
            bring_to_front(int(window.winId()))

    @Slot(str)
    @safe_slot
    def radialAction(self, name: str) -> None:  # noqa: N802
        if self._tour_active:
            self.practiced.emit(name)
            return
        actions = {
            "record": lambda: self.controller.toggle_recording(),
            "pause": self.controller.toggle_pause,
            "stop": self.controller.stop_recording,
            "mark": self.addMark,
            "claude": self.sendToAi,
            "transcript": self.openTranscript,
            "window": self.windowRequested.emit,
            "settings": self.settingsRequested.emit,
        }
        action = actions.get(name)
        if action:
            action()

    @Slot(float, float, int, float, result=int)
    def radialSector(self, dx: float, dy: float, count: int, dead_zone: float) -> int:  # noqa: N802
        return sector_for(dx, dy, count, dead_zone)

    @Slot(result=str)
    @safe_slot
    def recordedTitle(self) -> str:  # noqa: N802
        return self._transcript.title if self._transcript else ""

    # ---------- запись новой комбинации ----------

    # ---------- диктовка ----------

    dictationPhase = Property(str, lambda self: self._dictation_phase, notify=dictationChanged)  # noqa: N815
    dictationNote = Property(str, lambda self: self._dictation_note, notify=dictationChanged)  # noqa: N815
    dictationLevel = Property(float, lambda self: self._dictation_level, notify=dictationLevelChanged)  # noqa: N815

    def _on_dictation(self, phase: str) -> None:
        self._dictation_phase = phase
        if phase != "listening":
            self._dictation_level = 0.0
            self.dictationLevelChanged.emit()
        self.dictationChanged.emit()

    def _dictation_notice(self, text: str, ms: int = 3500) -> None:
        """Короткая подпись в плашке диктовки: окно Говорилки в этот момент обычно спрятано, тост не увидят."""
        self._dictation_note = text
        self.dictationChanged.emit()

        def clear() -> None:
            if self._dictation_note == text:
                self._dictation_note = ""
                self.dictationChanged.emit()

        QTimer.singleShot(ms, clear)

    def _on_dictation_text(self, text: str) -> None:
        if not text.strip():
            self._dictation_notice("Не расслышал. Говорите, пока держите клавиши", 2500)
            return
        clipboard = QGuiApplication.clipboard()
        saved = copy_mime(clipboard.mimeData())
        clipboard.setText(text)
        QTimer.singleShot(PASTE_DELAY_MS, lambda: self._paste_dictation(text, saved))

    def _paste_dictation(self, text: str, saved: QMimeData | None) -> None:
        paste = "⌘V" if sys.platform == "darwin" else "Ctrl+V"
        try:
            autopaste.paste_at_cursor()
        except autopaste.NoPermissionError:
            self._dictation_notice(f"Текст в буфере: нажмите {paste}. Чтобы вставлялось само, включите «Универсальный доступ»", 6000)
            return
        except Exception as exc:  # noqa: BLE001 — не вставилось: текст хотя бы в буфере
            log.warning("Диктовка не вставилась: %s", exc)
            self._dictation_notice(f"Текст в буфере: нажмите {paste}")
            return

        def restore() -> None:  # прежнее содержимое буфера возвращаем, если человек за это время ничего не скопировал
            clipboard = QGuiApplication.clipboard()
            if saved is not None and clipboard.text() == text:
                clipboard.setMimeData(saved)

        QTimer.singleShot(RESTORE_CLIPBOARD_MS, restore)

    captureTarget = Property(str, lambda self: self._capture_target, notify=captureChanged)  # noqa: N815

    @Slot()
    @safe_slot
    def startDictationCapture(self) -> None:  # noqa: N802
        """«Изменить…» у диктовки: следующая нажатая комбинация станет комбинацией диктовки."""
        self.startCapture()
        if self._capturing:
            self._capture_target = "dictation"
            self.captureChanged.emit()

    @Slot()
    @safe_slot
    def startCapture(self) -> None:  # noqa: N802
        self._capture_target = "hotkey"
        try:
            self._get_keyboard().snapshot()
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(f"Не получилось читать клавиши: {exc}", "error")
            return
        self.controller.hotkeys.pause()  # старая комбинация не должна срабатывать, пока задают новую
        self._recorder = ChordRecorder()
        self._capture_deadline = time.monotonic() + CAPTURE_TIMEOUT_S
        self._capturing = True
        self.captureChanged.emit()

    @Slot()
    @safe_slot
    def stopCapture(self) -> None:  # noqa: N802
        if self._recorder is not None:
            self._recorder = None
            self.controller.hotkeys.resume()
        self._capturing = False
        self.captureChanged.emit()

    @Slot(str, bool)
    @safe_slot
    def watchKeys(self, owner: str, on: bool) -> None:  # noqa: N802
        """Показываем, какие клавиши Говорилка видит прямо сейчас, пока это нужно хоть одному окну
        (настройкам, знакомству). Каждое окно включает и выключает только своё: закрытие одного
        не гасит подсветку в другом."""
        if on:
            self._key_watchers.add(owner)
            self._capture_timer.start()
            return
        self._key_watchers.discard(owner)
        if owner == "settings":
            self.stopCapture()  # запись новой комбинации живёт только в настройках
        if not self._key_watchers:
            self._capture_timer.stop()

    capturedHotkey = Signal(str, str, str)  # noqa: N815 — строка для настроек, подпись, предупреждение

    def _get_keyboard(self) -> Keyboard:
        if self._keyboard is None:
            self._keyboard = system_keyboard()
        return self._keyboard

    def _tick_hotkeys(self) -> None:
        self.controller.hotkeys.tick()

    def _tick_capture(self) -> None:
        if "nocapture" in os.environ.get("GOVORILKA_LIVE_FLAGS", ""):  # поиск сбоя в CI: без опроса клавиш в настройках
            return
        try:
            snap = self._get_keyboard().snapshot()
        except Exception as exc:  # noqa: BLE001
            self._live_keys = f"клавиши не читаются: {exc}"
            self.liveKeysChanged.emit()
            return
        text = format_snapshot(snap)
        if text != self._live_keys:
            self._live_keys = text
            self.liveKeysChanged.emit()
        if self._recorder is None:
            return
        chord = self._recorder.feed(snap)
        if chord is not None:
            warning = ""
            if conflicts_with_language_switch(chord, windows_language_switch()):
                warning = "Это же сочетание переключает язык ввода — лучше выбрать другое."
            other = getattr(self.controller.settings, "hotkey" if self._capture_target == "dictation" else "dictation_hotkey")
            if chord_to_string(chord) == other:
                warning = "Эта комбинация уже занята: у записи и диктовки они должны быть разными."
            captured = self.capturedDictationHotkey if self._capture_target == "dictation" else self.capturedHotkey
            captured.emit(chord_to_string(chord), format_chord(chord), warning)
            self.stopCapture()
        elif time.monotonic() > self._capture_deadline:
            self.stopCapture()
            self.toast.emit("Комбинацию не нажали — оставил прежнюю", "info")

    # ---------- события контроллера (уже в потоке окна) ----------

    def _on_state(self, _state: str) -> None:
        recording = self.controller.state in (AppState.RECORDING, AppState.PAUSED)
        if recording and not self._elapsed_timer.isActive():
            self._elapsed_timer.start()
        elif not recording:
            self._elapsed_timer.stop()
        if self.controller.state == AppState.IDLE:
            self._partials.clear()
            self._schedule_refresh()
        self._tick_elapsed()
        self.stateChanged.emit()

    loadChanged = Signal()  # noqa: N815
    loading = Property(bool, lambda self: self._loading, notify=loadChanged)
    loadFraction = Property(float, lambda self: self._load_fraction, notify=loadChanged)  # noqa: N815 — -1: неизвестно

    def _track_loading(self, text: str) -> None:
        """Скачивание и загрузка моделей — по строкам статуса: «Скачиваю …: 120 из 852 МБ (14%)», «Загружаю модели…»."""
        loading, fraction = False, -1.0
        if text.startswith("Скачиваю"):
            loading = True
            match = re.search(r"\((\d+)%\)", text)
            fraction = int(match.group(1)) / 100 if match else -1.0
        elif text == LOADING:
            loading = True
        if (loading, fraction) != (self._loading, self._load_fraction):
            self._loading, self._load_fraction = loading, fraction
            self.loadChanged.emit()

    def _set_status(self, text: str) -> None:
        self._track_loading(text)
        self._status = text
        self.statusChanged.emit()

    def _on_text(self, event: TextEvent) -> None:
        if event.kind == "partial":
            self._partials[event.source] = event
        else:
            self._partials.pop(event.source, None)
        self._schedule_refresh()

    def _on_level(self, source: str, level: float) -> None:
        if source == "dictation":
            self._dictation_level = min(1.0, level * 6)
            self.dictationLevelChanged.emit()
            return
        if source in self._levels:
            self._levels[source] = max(self._levels[source], min(1.0, level * 3))

    def _on_transcript(self, transcript: Transcript, path: Path) -> None:
        self._transcript = transcript
        self.summaryChanged.emit()
        self._partials.clear()
        self._model.clear()
        self.transcriptInfoChanged.emit()

    def _on_progress(self, value: float) -> None:
        self._progress = value
        job = self.controller.file_job
        self._file_name = job.source_path.name if job else ""
        self.progressChanged.emit()

    def _on_error(self, text: str) -> None:
        self._set_status(text)
        self.toast.emit(text, "error")

    def _on_permission(self, error: PermissionDeniedError) -> None:
        self._set_status(str(error))
        self.permissionRequested.emit(str(error), error.service, self.controller.can_reset_permission())

    def _on_hotkey(self) -> None:
        if self.controller.settings.hotkey_action == "record" and not self._tour_active:
            self.controller.toggle_recording()
            return
        position = QCursor.pos()
        self.radialRequested.emit(position.x(), position.y())

    def _on_finished(self, path: Path | None, message: str) -> None:
        self._set_status(message)
        self._progress = 0.0
        self._file_name = ""
        self.progressChanged.emit()
        self.transcriptInfoChanged.emit()
        self.toast.emit(message, "success" if path else "info")

    # ---------- таймеры ----------

    def _schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _refresh(self) -> None:
        transcript = self._transcript
        utterances = transcript.utterances if transcript else []
        names = transcript.display_name if transcript else (lambda voice: voice)
        self._model.update(utterances, list(self._partials.values()), names)

    def _tick_levels(self) -> None:
        changed = False
        for source, incoming in self._levels.items():
            shown = max(self._shown_levels[source] * LEVEL_DECAY, incoming)
            if abs(shown - self._shown_levels[source]) > 0.002:
                self._shown_levels[source] = shown
                changed = True
            self._levels[source] = 0.0
        if changed:
            self.levelsChanged.emit()

    def _tick_elapsed(self) -> None:
        text = format_offset(self.controller.elapsed())
        if text != self._elapsed:
            self._elapsed = text
            self.elapsedChanged.emit()


class _Relay(Listener):
    """Принимает события контроллера в любом потоке и пересылает сигналами в поток окна."""

    def __init__(self, backend: Backend) -> None:
        self._b = backend

    def on_state(self, state: AppState) -> None:
        self._b._state_in.emit(state.value)

    def on_status(self, text: str) -> None:
        self._b._status_in.emit(text)

    def on_text(self, event: TextEvent) -> None:
        self._b._text_in.emit(event)

    def on_level(self, source: str, level: float) -> None:
        self._b._level_in.emit(source, level)

    def on_transcript(self, transcript: Transcript, path: Path) -> None:
        self._b._transcript_in.emit(transcript, path)

    def on_file_progress(self, value: float) -> None:
        self._b._progress_in.emit(value)

    def on_error(self, text: str) -> None:
        self._b._error_in.emit(text)

    def on_permission(self, error: PermissionDeniedError) -> None:
        self._b._permission_in.emit(error)

    def on_hotkey(self) -> None:
        self._b._hotkey_in.emit()

    def on_hotkey_release(self) -> None:
        self._b._release_in.emit()

    def on_dictation(self, phase: str) -> None:
        self._b._dictation_in.emit(phase)

    def on_assistant(self, kind: str, text: str) -> None:
        self._b._assistant_in.emit(kind, text)

    def on_dictation_text(self, text: str) -> None:
        self._b._dictation_text_in.emit(text)

    def on_models_unloaded(self) -> None:
        pass  # веса освобождаются сами, как только на них не остаётся ссылок: явная сборка мусора не нужна

    def on_update(self, info: object | None, error: str | None, manual: bool) -> None:
        self._b._update_in.emit(info, error or "", manual)

    def on_finished(self, path: Path | None, message: str) -> None:
        self._b._finished_in.emit(path, message)


def _repeating(parent: QObject, interval: int, slot) -> QTimer:  # noqa: ANN001
    timer = QTimer(parent)
    timer.setInterval(interval)
    timer.timeout.connect(slot)
    return timer


def _single_shot(parent: QObject, interval: int, slot) -> QTimer:  # noqa: ANN001
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval)
    timer.timeout.connect(slot)
    return timer
