"""Мост между контроллером и QML.

Контроллер шлёт события из фоновых потоков; здесь они превращаются в сигналы Qt и попадают
в поток интерфейса. QML видит только свойства и слоты этого объекта.
"""

from __future__ import annotations

import functools
import logging
import math
import sys
import time
from dataclasses import asdict, fields, replace
from pathlib import Path

from PySide6.QtCore import Property, QObject, QPoint, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication

from govorilka.audio_base import PermissionDeniedError
from govorilka.config import MODELS, SOURCE_MIC, SOURCE_SYSTEM, Settings
from govorilka.controller import AppState, Controller, Listener
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
from govorilka.qt.models import TranscriptModel
from govorilka.transcript import Transcript, format_offset

log = logging.getLogger(__name__)

LEVEL_TICK_MS = 33  # индикаторы уровня — 30 кадров в секунду
LEVEL_DECAY = 0.86
ELAPSED_TICK_MS = 200
REFRESH_MS = 50  # реплики, пришедшие почти одновременно, рисуем одним обновлением
CAPTURE_TICK_MS = 40
CAPTURE_TIMEOUT_S = 10.0


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


def settings_to_map(settings: Settings) -> dict[str, object]:
    data = asdict(settings)
    try:
        data["hotkeyLabel"] = format_chord(parse_chord(settings.hotkey))
    except ValueError:
        data["hotkeyLabel"] = settings.hotkey
    return data


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
            changes[field.name] = str(value).strip() or default
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
    _finished_in = Signal(object, str)

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
        self._recorder: ChordRecorder | None = None
        self._capture_deadline = 0.0
        self._keyboard: Keyboard | None = None
        self._tray_available = False
        self._startup_notices: list[str] = []

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
        ):
            signal.connect(slot)
        # контроллер создаём после подключения сигналов: его первое сообщение («горячая клавиша не включена») не пропадёт
        self.controller: Controller = self._controller_factory(self._listener)

        self._refresh_timer = _single_shot(self, REFRESH_MS, self._refresh)
        self._level_timer = QTimer(self, interval=LEVEL_TICK_MS, timeout=self._tick_levels)
        self._level_timer.start()
        self._elapsed_timer = QTimer(self, interval=ELAPSED_TICK_MS, timeout=self._tick_elapsed)
        self._capture_timer = QTimer(self, interval=CAPTURE_TICK_MS, timeout=self._tick_capture)

    def start(self) -> None:
        self.controller.start()
        self._startup_notices = self.controller.startup_notices()

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
        return [{"id": key, "label": label} for key, label in MODELS.items()]

    models = Property("QVariantList", _get_models, constant=True)

    autostart = Property(bool, lambda self: self.controller.autostart_enabled(), notify=settingsChanged)
    inputMonitoringMissing = Property(  # noqa: N815
        bool, lambda self: input_monitoring_status() is False, notify=settingsChanged
    )

    # ---------- действия из QML ----------

    @Slot(str)
    @safe_slot
    def toggleRecording(self, title: str = "") -> None:  # noqa: N802
        self.controller.toggle_recording(title)

    @Slot()
    @safe_slot
    def togglePause(self) -> None:  # noqa: N802
        self.controller.toggle_pause()

    @Slot()
    @safe_slot
    def stopRecording(self) -> None:  # noqa: N802
        self.controller.stop_recording()

    @Slot()
    @safe_slot
    def addMark(self) -> None:  # noqa: N802
        if not self.controller.add_mark():
            self.toast.emit("Отметка ставится во время записи", "info")

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
    def sendToClaude(self) -> None:  # noqa: N802
        result = self.controller.send_to_claude()
        if result.clipboard:
            QGuiApplication.clipboard().setText(result.clipboard)
        self.toast.emit(result.message, "success" if result.ok else "error")

    @Slot()
    @safe_slot
    def openFolder(self) -> None:  # noqa: N802
        self.controller.open_folder()

    @Slot()
    @safe_slot
    def openTranscript(self) -> None:  # noqa: N802
        if not self.controller.open_transcript():
            self.toast.emit("Транскрипта пока нет", "info")

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
            "claude": self.sendToClaude,
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

    @Slot()
    @safe_slot
    def startCapture(self) -> None:  # noqa: N802
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

    @Slot(bool)
    @safe_slot
    def watchKeys(self, on: bool) -> None:  # noqa: N802
        """Пока открыты настройки, показываем, какие клавиши Говорилка видит прямо сейчас."""
        if on:
            self._capture_timer.start()
        else:
            self._capture_timer.stop()
            self.stopCapture()

    capturedHotkey = Signal(str, str, str)  # noqa: N815 — строка для настроек, подпись, предупреждение

    def _get_keyboard(self) -> Keyboard:
        if self._keyboard is None:
            self._keyboard = system_keyboard()
        return self._keyboard

    def _tick_capture(self) -> None:
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
            self.capturedHotkey.emit(chord_to_string(chord), format_chord(chord), warning)
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

    def _set_status(self, text: str) -> None:
        self._status = text
        self.statusChanged.emit()

    def _on_text(self, event: TextEvent) -> None:
        if event.kind == "partial":
            self._partials[event.source] = event
        else:
            self._partials.pop(event.source, None)
        self._schedule_refresh()

    def _on_level(self, source: str, level: float) -> None:
        if source in self._levels:
            self._levels[source] = max(self._levels[source], min(1.0, level * 3))

    def _on_transcript(self, transcript: Transcript, path: Path) -> None:
        self._transcript = transcript
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
        utterances = self._transcript.utterances if self._transcript else []
        self._model.update(utterances, list(self._partials.values()))

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

    def on_finished(self, path: Path | None, message: str) -> None:
        self._b._finished_in.emit(path, message)


def _single_shot(parent: QObject, interval: int, slot) -> QTimer:  # noqa: ANN001
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval)
    timer.timeout.connect(slot)
    return timer
