"""Интерфейс на Qt без экрана: иконки, модель ленты, мост к контроллеру, загрузка окон QML, трей."""

import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from conftest import pump  # noqa: E402
from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from govorilka.config import Settings  # noqa: E402
from govorilka.controller import AppState, Controller  # noqa: E402
from govorilka.pipeline import TextEvent  # noqa: E402
from govorilka.qt.backend import Backend, map_to_settings, sector_for, settings_to_map, to_path  # noqa: E402
from govorilka.qt.icons import ICONS, IconProvider, render, svg  # noqa: E402
from govorilka.qt.models import ME, SpeakerColors, TranscriptModel, build_rows  # noqa: E402
from govorilka.transcript import SOURCE_MARK, SOURCE_NOTE, Transcript, Utterance  # noqa: E402


class NoHotkeys:
    def __init__(self):
        self.paused = False

    def set_chord(self, chord): ...
    def start(self): ...
    def stop(self): ...
    def pause(self): ...
    def resume(self): ...


@pytest.fixture
def backend(qapp, tmp_path):
    settings = Settings(transcripts_dir=str(tmp_path))
    created = Backend(lambda listener: Controller(listener, settings, hotkeys=NoHotkeys()))
    yield created
    # Объект, который тест бросил, живёт в цикле событий Qt и получает таймеры уже после конца теста: в долгом
    # ожидании другого теста это давало случайное падение. Убираем его явно.
    from PySide6.QtCore import QEvent

    created.shutdown()
    created.setParent(None)
    created.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def speech(who, start, text, source="system"):
    return Utterance(who, start, text, start + 2, source)


# ---------- иконки ----------


def test_every_icon_renders_with_the_requested_color(qapp):
    for name in ICONS:
        image = render(name, QColor("#ff0000"), 48)
        visible = [image.pixelColor(x, y) for x in range(0, 48, 2) for y in range(0, 48, 2) if image.pixelColor(x, y).alpha() > 200]
        assert visible, name
        assert all(c.red() > 200 and c.green() < 60 and c.blue() < 60 for c in visible), name


def test_icon_provider_accepts_color_with_and_without_hash(qapp):
    from PySide6.QtCore import QSize

    provider = IconProvider()
    for color in ("00ff00", "#00ff00"):
        size = QSize()
        image = provider.requestImage(f"mic/{color}", size, QSize(32, 32))
        assert image.width() == 32 and size.width() == 32
        assert any(image.pixelColor(16, y).green() > 200 for y in range(32))


def test_unknown_icon_does_not_crash(qapp):
    from PySide6.QtCore import QSize

    assert not IconProvider().requestImage("нет-такой/ffffff", QSize(), QSize(24, 24)).isNull()
    assert b"viewBox" in svg("mic", "#ffffff")


# ---------- модель ленты ----------


def test_rows_group_same_speaker_and_color_is_stable():
    colors = SpeakerColors()
    rows = build_rows(
        [
            speech("Собеседник 1", 1, "а"),
            speech("Собеседник 1", 4, "б"),
            speech("Я", 8, "в", "mic"),
            speech("Собеседник 2", 12, "г"),
            speech("Собеседник 1", 16, "д"),
        ],
        [],
        colors,
    )
    assert [r.first for r in rows] == [True, False, True, True, True]
    assert [r.color for r in rows] == [0, 0, ME, 1, 0]  # первый собеседник всегда один и тот же цвет


def test_marks_and_notes_have_their_own_rows_and_break_groups():
    rows = build_rows(
        [
            speech("Собеседник 1", 1, "а"),
            Utterance("", 5, "Баги по оплате", 5, SOURCE_MARK),
            speech("Собеседник 1", 9, "б"),
            Utterance("", 12, "Пауза", 12, SOURCE_NOTE),
        ],
        [],
        SpeakerColors(),
    )
    assert [r.kind for r in rows] == ["speech", "mark", "speech", "note"]
    assert rows[2].first  # после отметки имя показываем снова


def test_partial_goes_last_and_empty_partials_are_skipped():
    partial = TextEvent("system", "Собеседник 1", "partial", "недосказанное", 20, 22)
    blank = TextEvent("mic", "Я", "partial", "", 21, 22)
    rows = build_rows([speech("Собеседник 1", 1, "а")], [blank, partial], SpeakerColors())
    assert [r.kind for r in rows] == ["speech", "partial"]
    assert rows[-1].text == "недосказанное"


def test_model_appends_only_the_tail(qapp):
    model = TranscriptModel()
    inserted, removed = [], []
    model.rowsInserted.connect(lambda parent, first, last: inserted.append((first, last)))
    model.rowsRemoved.connect(lambda parent, first, last: removed.append((first, last)))
    items = [speech("Собеседник 1", 1, "а")]
    model.update(items, [])
    items = [*items, speech("Я", 5, "б", "mic")]
    model.update(items, [])
    assert inserted == [(0, 0), (1, 1)] and removed == []  # вторая реплика — вставка одной строки, а не перерисовка всего


def test_model_replaces_partial_with_final_without_touching_earlier_rows(qapp):
    model = TranscriptModel()
    first = speech("Собеседник 1", 1, "а")
    partial = TextEvent("system", "Собеседник 1", "partial", "что-то", 5, 7)
    model.update([first], [partial])
    changes = []
    model.rowsRemoved.connect(lambda parent, a, b: changes.append(("removed", a, b)))
    model.rowsInserted.connect(lambda parent, a, b: changes.append(("inserted", a, b)))
    model.update([first, speech("Собеседник 1", 5, "что-то сказал")], [])
    assert changes == [("removed", 1, 1), ("inserted", 1, 1)]  # изменился только хвост
    assert model.rowCount() == 2


def test_model_exposes_roles_to_qml(qapp):
    model = TranscriptModel()
    model.update([speech("Собеседник 1", 65, "текст")], [])
    names = {bytes(n).decode() for n in model.roleNames().values()}
    assert {"kind", "time", "speaker", "text", "colorIndex", "first"} <= names
    index = model.index(0, 0)
    by_name = {bytes(n).decode(): role for role, n in model.roleNames().items()}
    assert model.data(index, by_name["text"]) == "текст" and model.data(index, by_name["time"]) == "01:05"


# ---------- настройки ----------


def test_settings_roundtrip_through_qml_map():
    original = Settings(hotkey="alt+cmd", hotkey_action="record", theme="light")
    data = settings_to_map(original)
    assert data["hotkeyLabel"] and data["hotkey_action"] == "record"
    assert map_to_settings(Settings(), data) == original


def test_settings_from_qml_ignore_wrong_types_and_unknown_fields():
    result = map_to_settings(Settings(), {"capture_mic": 0, "theme": "  ", "hotkey": "ctrl+alt+r", "левое": 1})
    assert result.capture_mic is False and result.theme == "system" and result.hotkey == "ctrl+alt+r"


def test_file_url_becomes_a_path():
    if sys.platform == "win32":
        assert to_path("file:///C:/Users/me/Мой файл.mp3") == Path("C:/Users/me/Мой файл.mp3")
    else:
        assert to_path("file:///Users/me/Мой файл.mp3") == Path("/Users/me/Мой файл.mp3")
    assert to_path("/tmp/a.wav") == Path("/tmp/a.wav")


# ---------- круговое меню ----------


@pytest.mark.parametrize(
    ("dx", "dy", "expected"),
    [(0, -100, 0), (100, 0, 1), (0, 100, 2), (-100, 0, 3), (35, -90, 0), (90, -35, 1), (10, 5, -1)],
)
def test_sector_by_direction(dx, dy, expected):
    assert sector_for(dx, dy, 4, 40) == expected


def test_sector_zero_looks_up_and_goes_clockwise_for_six():
    assert [sector_for(100 * s, -100 * c, 6, 40) for s, c in [(0, 1), (0.87, 0.5), (0.87, -0.5), (0, -1), (-0.87, -0.5), (-0.87, 0.5)]] == [0, 1, 2, 3, 4, 5]


def test_sector_edge_cases():
    assert sector_for(0, 0, 6, 40) == -1 and sector_for(100, 0, 0, 40) == -1


# ---------- мост к контроллеру ----------


def test_events_from_background_threads_reach_the_ui_thread(backend, qapp):
    transcript = Transcript(datetime(2026, 1, 1), "Тема")
    transcript.add(speech("Собеседник 1", 3, "привет"))
    done = threading.Event()

    def worker():  # так события приходят из потоков записи
        backend._listener.on_transcript(transcript, Path("a.md"))
        backend._listener.on_text(TextEvent("system", "Собеседник 1", "final", "привет", 3, 5))
        done.set()

    threading.Thread(target=worker).start()
    assert pump(qapp, lambda: backend.transcript.rowCount() == 1)
    assert done.is_set() and backend.transcriptTitle == "Тема"


def test_state_change_updates_properties_and_timer(backend, qapp):
    seen = []
    backend.stateChanged.connect(lambda: seen.append(backend.state))
    backend.controller.state = AppState.RECORDING
    backend._listener.on_state(AppState.RECORDING)
    assert pump(qapp, lambda: seen)
    assert backend.state == "recording" and backend._elapsed_timer.isActive()
    backend.controller.state = AppState.IDLE
    backend._listener.on_state(AppState.IDLE)
    assert pump(qapp, lambda: not backend._elapsed_timer.isActive())


def test_levels_decay_smoothly_after_a_loud_moment(backend, qapp):
    backend._listener.on_level("mic", 0.4)
    assert pump(qapp, lambda: backend.micLevel > 0.5)  # ×3: тихий звук должен быть заметен на индикаторе
    assert pump(qapp, lambda: backend.micLevel < 0.05, timeout=4)


def test_hotkey_opens_the_radial_menu_at_the_cursor_or_records_directly(backend, qapp):
    opened = []
    backend.radialRequested.connect(lambda x, y: opened.append((x, y)))
    backend._listener.on_hotkey()
    assert pump(qapp, lambda: opened)
    toggled = []
    backend.controller.toggle_recording = lambda title="": toggled.append(title)
    backend.controller.settings = Settings(hotkey_action="record")
    backend._listener.on_hotkey()
    assert pump(qapp, lambda: toggled == [""]) and len(opened) == 1


def test_permission_error_reaches_qml_with_reset_flag(backend, qapp):
    from govorilka.audio_base import PermissionDeniedError

    asked = []
    backend.permissionRequested.connect(lambda text, service, can_reset: asked.append((text, service, can_reset)))
    backend._listener.on_permission(PermissionDeniedError("ScreenCapture", "Нет доступа"))
    assert pump(qapp, lambda: asked) and asked[0][:2] == ("Нет доступа", "ScreenCapture")


def test_tour_practice_never_runs_the_chosen_action(backend, qapp):
    calls, practiced = [], []
    backend.controller.toggle_recording = lambda title="": calls.append("record")
    backend.practiced.connect(practiced.append)
    backend.setTourActive(True)
    backend.radialAction("record")
    assert calls == [] and practiced == ["record"]
    backend.setTourActive(False)
    backend.radialAction("record")
    assert calls == ["record"]


def test_tour_practice_opens_the_menu_even_when_the_hotkey_starts_recording(backend, qapp):
    backend.controller.settings = Settings(hotkey_action="record")
    calls, opened = [], []
    backend.controller.toggle_recording = lambda title="": calls.append("record")
    backend.radialRequested.connect(lambda x, y: opened.append((x, y)))
    backend.setTourActive(True)
    backend._on_hotkey()
    assert opened and calls == []


def test_finishing_the_tour_is_remembered(backend, qapp, monkeypatch):
    from govorilka import controller as controller_module

    monkeypatch.setattr(controller_module, "save_settings", lambda s: None)
    assert backend.settings["tour_done"] is False
    backend.setTourActive(True)
    backend.finishTour()
    assert backend.settings["tour_done"] is True
    assert backend._tour_active is False


def test_radial_actions_call_the_controller(backend, qapp):
    calls = []
    backend.controller.toggle_pause = lambda: calls.append("pause")
    backend.controller.stop_recording = lambda: calls.append("stop")
    backend.controller.toggle_recording = lambda title="": calls.append("record")
    for name in ("pause", "stop", "record", "нет-такого"):
        backend.radialAction(name)
    assert calls == ["pause", "stop", "record"]
    shown = []
    backend.windowRequested.connect(lambda: shown.append(1))
    backend.radialAction("window")
    assert shown == [1]


def test_mark_without_recording_explains_itself(backend, qapp):
    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append((text, kind)))
    backend.addMark()
    assert toasts and "во время записи" in toasts[0][0]


def test_applying_settings_saves_and_reports(backend, qapp, monkeypatch):
    from govorilka import controller as controller_module

    monkeypatch.setattr(controller_module, "save_settings", lambda s: None)
    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append((text, kind)))
    backend.applySettings({"theme": "dark", "hotkey": "ctrl+alt+r", "capture_mic": False}, False)
    assert backend.controller.settings.theme == "dark" and backend.controller.settings.capture_mic is False
    assert toasts[-1][1] == "success"


# ---------- окна QML ----------


def test_qml_windows_load_without_warnings(backend, qapp):
    from govorilka.qt.app import create_engine

    engine = create_engine(backend, strict=True)
    root = engine.rootObjects()[0]
    for name in ("settings", "radial"):
        assert root.findChild(QObject, name) is not None, name
    assert root.property("title") == "Говорилка"


def test_broken_qml_fails_loudly_instead_of_showing_an_empty_window(backend, qapp, monkeypatch, tmp_path):
    from govorilka.qt import app as qt_app

    (tmp_path / "Main.qml").write_text("import QtQuick\nItem { nonsense }", encoding="utf-8")
    monkeypatch.setattr(qt_app, "QML_DIR", tmp_path)
    with pytest.raises(qt_app.QmlLoadError):
        qt_app.create_engine(backend)


def test_qml_files_use_only_integer_pixel_sizes_and_known_icons():
    """Ловим опечатки, которые Qt прощает молча: дробный шрифт и несуществующая иконка."""
    import re

    qml = Path(__file__).resolve().parent.parent / "govorilka" / "qml"
    for path in qml.glob("*.qml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"pixelSize:\s*\d+\.\d", text), path.name
        for name in re.findall(r'(?:iconName|name):\s*"([a-z]+)"', text):
            assert name in ICONS or name in {"idle"}, (path.name, name)


# ---------- трей ----------


def test_tray_menu_follows_the_state(backend, qapp):
    from govorilka.qt.tray import Tray

    tray = Tray(backend, lambda: None, lambda: None, lambda: None)

    def labels():
        tray._rebuild()
        return [a.text() for a in tray._menu.actions() if a.text()]

    idle = labels()
    assert "Начать запись" in idle and "Пауза" not in idle
    backend.controller.state = AppState.RECORDING
    live = labels()
    assert "Остановить запись" in live and "Пауза" in live and "Поставить отметку ★" in live
    backend.controller.state = AppState.PAUSED
    assert "Продолжить" in labels()
    backend.controller.state = AppState.STARTING  # пока запись запускается, начать вторую нельзя
    tray._rebuild()
    starting = [a for a in tray._menu.actions() if a.text() == "Начать запись"]
    assert starting and not starting[0].isEnabled()


def test_tray_icons_exist_for_every_state(qapp):
    from govorilka.qt.tray import tray_icon

    for state in ("idle", "recording", "paused"):
        assert not tray_icon(state).isNull()


# ---------- тихие ошибки QML ----------


def _qml_messages(qapp, action) -> list[str]:
    """Qt молча теряет привязки, которые ссылаются на несуществующее (чужой id, опечатка): пишет в журнал,
    а окно просто выглядит сломанным. Собираем эти сообщения, пока выполняется действие."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    messages: list[str] = []
    previous = qInstallMessageHandler(
        lambda kind, context, text: messages.append(text) if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg) else None
    )
    try:
        action()
        for _ in range(25):
            qapp.processEvents()
    finally:
        qInstallMessageHandler(previous)
    return messages


BAD = ("ReferenceError", "TypeError", "is not defined", "Unable to assign", "Cannot read", "Binding loop", "Cannot assign")


def test_no_qml_runtime_errors_in_any_window_or_state(backend, qapp):
    from govorilka.qt.app import create_engine

    engine = create_engine(backend)
    root = engine.rootObjects()[0]

    def exercise():
        root.show()
        for state in (AppState.RECORDING, AppState.PAUSED, AppState.FILE, AppState.IDLE):
            backend.controller.state = state
            backend.stateChanged.emit()
            qapp.processEvents()
        transcript = Transcript(datetime(2026, 1, 1), "Тема")
        for i, (who, source) in enumerate([("Собеседник 1", "system"), ("Я", "mic"), ("Собеседник 2", "system")]):
            transcript.add(speech(who, i * 5, "Текст реплики " + str(i), source))
        transcript.add(Utterance("", 20, "Баги по оплате", 20, SOURCE_MARK))
        backend._on_transcript(transcript, Path("a.md"))
        backend._partials["system"] = TextEvent("system", "Собеседник 1", "partial", "недосказанное", 25, 27)
        backend._refresh()
        backend._listener.on_permission(__import__("govorilka.audio_base", fromlist=["x"]).PermissionDeniedError("ScreenCapture", "Нет доступа"))
        backend.toast.emit("Готово", "success")
        backend.toast.emit("Ошибка", "error")
        root.findChild(QObject, "settings").open()
        backend.radialRequested.emit(300, 300)
        backend.controller.state = AppState.RECORDING
        backend.stateChanged.emit()
        for theme in ("light", "dark"):
            backend.controller.settings = Settings(theme=theme, reduce_motion=theme == "light")
            backend.settingsChanged.emit()
            qapp.processEvents()

    problems = [m for m in _qml_messages(qapp, exercise) if any(bad in m for bad in BAD)]
    assert problems == []


def test_recording_pill_shows_only_while_recording_with_the_main_window_out_of_the_way(backend, qapp):
    from govorilka.qt.app import create_engine

    engine = create_engine(backend)
    root = engine.rootObjects()[0]
    pill = root.findChild(QObject, "pill")
    assert pill is not None
    backend.controller.settings = Settings(show_indicator=True)
    backend.settingsChanged.emit()
    root.hide()
    backend.controller.state = AppState.RECORDING
    backend.stateChanged.emit()
    assert pump(qapp, lambda: pill.property("visible") is True), "плашка не показалась, хотя запись идёт, а окно скрыто"
    backend.controller.state = AppState.IDLE
    backend.stateChanged.emit()
    assert pump(qapp, lambda: pill.property("visible") is False)


def test_radial_menu_opens_even_when_the_main_window_is_hidden(backend, qapp):
    """Главное окно в трее — меню по горячей клавише всё равно должно появиться."""
    from govorilka.qt.app import create_engine

    engine = create_engine(backend)
    root = engine.rootObjects()[0]
    radial = root.findChild(QObject, "radial")
    root.hide()
    backend.radialRequested.emit(400, 300)
    assert pump(qapp, lambda: radial.property("visible") is True), "круговое меню не открылось при скрытом главном окне"
    assert radial.property("width") >= 400


# ---------- подписи говорящих в ленте ----------


def test_rows_keep_grouping_and_color_after_a_speaker_is_renamed(qapp):
    colors = SpeakerColors()
    before = [speech("Собеседник 2", 1, "а"), speech("Собеседник 1", 5, "б"), speech("Собеседник 2", 9, "в")]
    rows_before = build_rows(before, [], colors)
    transcript = Transcript(datetime(2026, 1, 1), "т")
    for u in before:
        transcript.add(Utterance(u.speaker, u.start, u.text, u.end, u.source, u.speaker))
    transcript.rename_voice("Собеседник 2", "Аня")
    rows_after = build_rows(transcript.utterances, [], colors)
    assert [r.speaker for r in rows_after] == ["Аня", "Собеседник 1", "Аня"]
    assert [r.color for r in rows_after] == [r.color for r in rows_before]  # имя не перекрашивает человека
    assert [r.voice_id for r in rows_after] == ["Собеседник 2", "Собеседник 1", "Собеседник 2"]


def test_backend_rename_updates_the_feed_and_confirms(backend, qapp, tmp_path, monkeypatch):
    from govorilka import controller as controller_module

    monkeypatch.setattr(controller_module, "save_settings", lambda s: None)
    transcript = Transcript(datetime(2026, 1, 1), "т")
    transcript.add(Utterance("Собеседник 1", 3, "привет", 5, "system", "Собеседник 1"))
    backend.controller.transcript = transcript
    backend.controller.last_file = tmp_path / "x.md"
    backend._on_transcript(transcript, tmp_path / "x.md")
    backend._refresh()
    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append((text, kind)))
    backend.renameSpeaker("Собеседник 1", "Аня")
    assert pump(qapp, lambda: backend.transcript.rows[0].speaker == "Аня")
    assert toasts and "Аня" in toasts[0][0]
    backend.renameSpeaker("Собеседник 1", "   ")  # пустое имя игнорируется молча
    assert backend.transcript.rows[0].speaker == "Аня"


def test_forgetting_voices_reports_the_count(backend, qapp, tmp_path, monkeypatch):
    import numpy as np

    from govorilka import controller as controller_module
    from govorilka.voices import VoiceBook

    monkeypatch.setattr(controller_module, "DATA_DIR", tmp_path)
    book = VoiceBook(tmp_path / "voices.json")
    book.remember("Аня", np.ones(256, np.float32))
    book.save()
    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append(text))
    assert backend.knownVoices == 1
    backend.forgetVoices()
    assert backend.knownVoices == 0 and toasts[-1] == "Забыл голосов: 1"


def test_key_watch_stays_on_while_any_window_needs_it(backend, qapp):
    backend.watchKeys("settings", True)
    backend.watchKeys("tour", True)
    backend.watchKeys("settings", False)
    assert backend._capture_timer.isActive()  # закрыли настройки — подсветка в знакомстве живёт
    backend.watchKeys("tour", False)
    assert not backend._capture_timer.isActive()


def test_start_button_never_stops_a_running_recording(backend, qapp):
    calls = []
    backend.controller.start_recording = lambda title="": calls.append("start")
    backend.controller.state = AppState.RECORDING
    backend.startRecording("")
    assert calls == []
    backend.controller.state = AppState.IDLE
    backend.startRecording("")
    assert calls == ["start"]


def test_pause_and_mark_explain_themselves_when_nothing_is_recorded(backend, qapp):
    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append(text))
    backend.togglePause()
    backend.addMark()
    assert len(toasts) == 2 and "во время записи" in toasts[0] and "важный момент" in toasts[1]


def test_claude_workdir_can_be_cleared_but_other_texts_keep_their_value():
    current = Settings(claude_workdir="C:/work", transcripts_dir="C:/t")
    result = map_to_settings(current, {"claude_workdir": "  ", "transcripts_dir": ""})
    assert result.claude_workdir == "" and result.transcripts_dir == "C:/t"


def test_window_action_brings_back_a_minimized_window(qapp, tmp_path):
    from PySide6.QtGui import QWindow

    from govorilka.qt.app import create_engine

    settings = Settings(transcripts_dir=str(tmp_path), tour_done=True)
    backend = Backend(lambda listener: Controller(listener, settings, hotkeys=NoHotkeys()))
    engine = create_engine(backend, strict=True)
    window = engine.rootObjects()[0]
    assert pump(qapp, window.isExposed)
    window.showMinimized()
    assert pump(qapp, lambda: window.visibility() == QWindow.Visibility.Minimized)
    backend.windowRequested.emit()  # то же, что «Окно» в круговом меню и в трее
    assert pump(qapp, lambda: window.visibility() != QWindow.Visibility.Minimized and window.isVisible())
    window.hide()
    assert pump(qapp, lambda: not window.isVisible())
    backend.windowRequested.emit()  # окно было убрано в трей
    assert pump(qapp, window.isVisible)
    window.hide()
    engine.deleteLater()


def test_mac_overlay_sets_the_three_window_properties_in_order():
    from govorilka.qt import mac_overlay

    calls = []

    def send(receiver, selector, restype=None, argtypes=(), args=()):
        calls.append((selector, args))
        return 1234 if selector == "window" else None

    assert mac_overlay.apply(77, send=send)
    assert [c[0] for c in calls] == ["window", "styleMask", "setStyleMask:", "setHidesOnDeactivate:", "setCollectionBehavior:", "setLevel:"]
    assert calls[2][1][0] & mac_overlay.NONACTIVATING_PANEL  # панель не активирует приложение
    assert calls[3][1] == (False,)  # не прятать, когда приложение неактивно
    behavior = calls[4][1][0]
    assert behavior & mac_overlay.CAN_JOIN_ALL_SPACES and behavior & mac_overlay.FULL_SCREEN_AUXILIARY
    assert calls[5][1] == (mac_overlay.LEVEL_POP_UP_MENU,)


def test_mac_overlay_never_breaks_the_app():
    from govorilka.qt import mac_overlay

    assert mac_overlay.apply(77, send=lambda *a, **k: None) is False  # у вида нет окна
    assert mac_overlay.apply(77, send=lambda *a, **k: (_ for _ in ()).throw(OSError("нет"))) is False
    if sys.platform != "darwin":
        assert mac_overlay.apply(77) is False  # вне macOS ничего не делает


def test_access_rows_are_empty_outside_macos_and_know_all_four_permissions(backend, qapp):
    from govorilka import permissions

    assert [row[0] for row in permissions.ACCESS_ROWS] == ["microphone", "screen", "input", "accessibility"]
    if sys.platform != "darwin":
        assert backend.access == [] and backend.accessAvailable is False
        assert set(permissions.access_status().values()) == {None}


def test_privacy_links_point_to_the_right_system_settings_pane():
    from govorilka import permissions

    assert permissions.privacy_link("Microphone") == "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
    assert permissions.privacy_link("ListenEvent").endswith("Privacy_ListenEvent")


def test_reset_all_resets_every_service_and_reports_failures(monkeypatch):
    from govorilka import permissions

    seen = []
    monkeypatch.setattr(permissions, "reset_permission", lambda service, bundle_id=permissions.BUNDLE_ID: seen.append(service) or (None if service != "Accessibility" else "нет прав"))
    problems = permissions.reset_all()
    assert seen == list(permissions.ALL_SERVICES) and problems == ["Accessibility: нет прав"]


def test_reset_all_from_the_app_quits_only_when_everything_was_reset(backend, qapp, monkeypatch):
    from govorilka import permissions

    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append(kind))
    monkeypatch.setattr(permissions, "can_reset", lambda: True)
    monkeypatch.setattr(permissions, "reset_all", lambda: ["Microphone: не вышло"])
    backend.resetAllPermissions()
    assert toasts == ["error"]  # не вышло — не закрываем приложение, человек видит причину


def test_microphone_and_automation_statuses_are_decoded():
    from govorilka import permissions

    assert permissions.decode_microphone(3) is True  # разрешено
    assert permissions.decode_microphone(2) is False and permissions.decode_microphone(1) is False
    assert permissions.decode_microphone(0) is None and permissions.decode_microphone(None) is None
    assert permissions.decode_automation(0) is True and permissions.decode_automation(-1743) is False
    assert permissions.decode_automation(-1744) is None and permissions.decode_automation(-600) is None


def test_allow_button_first_asks_then_opens_the_pane_on_the_next_click(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import permissions
    from govorilka.qt import backend as backend_module

    asked, opened = [], []
    monkeypatch.setattr(permissions, "request", asked.append)
    monkeypatch.setattr(backend_module.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    monkeypatch.setattr(permissions, "access_status", lambda: {"screen": False, "microphone": True})
    backend.requestAccess("screen")
    assert pump(qapp, lambda: asked == ["screen"])
    pump(qapp, lambda: False, timeout=0.3)
    assert opened == []  # системный запрос на экране: раздел настроек поверх него не открываем
    backend.requestAccess("screen")
    assert pump(qapp, lambda: opened)
    assert asked == ["screen"] and opened[0].endswith("Privacy_ScreenCapture")  # запрос не повторяется, открылся нужный раздел
    opened.clear()
    backend.requestAccess("microphone")  # разрешили в ответ на запрос — настройки не нужны
    assert pump(qapp, lambda: asked.count("microphone") == 1)
    pump(qapp, lambda: False, timeout=0.3)
    assert opened == []
    backend.requestAccess("нет-такого")  # неизвестная служба игнорируется
    assert "нет-такого" not in asked


def test_denied_microphone_opens_the_pane_right_after_the_prompt_was_refused(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import permissions
    from govorilka.qt import backend as backend_module

    opened = []
    monkeypatch.setattr(permissions, "request", lambda key: None)
    monkeypatch.setattr(backend_module.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    monkeypatch.setattr(permissions, "access_status", lambda: {"microphone": False})
    backend.requestAccess("microphone")
    assert pump(qapp, lambda: opened) and opened[0].endswith("Privacy_Microphone")


def test_quit_request_lets_the_window_close_instead_of_hiding_to_tray(backend, qapp):
    assert backend.quitting is False
    backend.begin_quit()
    assert backend.quitting is True  # окно в этот момент закрывается по-настоящему
    backend._end_quit()
    assert backend.quitting is False


def test_autostart_refuses_a_path_that_will_not_exist_after_reboot():
    from govorilka import autostart

    assert autostart.unstable_location("/private/var/folders/x/AppTranslocation/ABC/d/Govorilka.app/Contents/MacOS/Govorilka")
    assert autostart.unstable_location("/Volumes/Govorilka/Govorilka.app/Contents/MacOS/Govorilka")
    assert not autostart.unstable_location("/Applications/Govorilka.app/Contents/MacOS/Govorilka")



def test_loading_indicator_follows_the_download_status(backend, qapp):
    states = []
    backend.loadChanged.connect(lambda: states.append((backend.loading, round(backend.loadFraction, 2))))
    backend._set_status("Скачиваю модель распознавания: 120 из 852 МБ (14%)")
    assert backend.loading is True and backend.loadFraction == 0.14
    backend._set_status("Скачиваю детектор речи: 1 МБ…")  # размер неизвестен — кольцо крутится
    assert backend.loading is True and backend.loadFraction == -1.0
    from govorilka.engines import LOADING

    backend._set_status(LOADING)
    assert backend.loading is True and backend.loadFraction == -1.0
    backend._set_status("Модели готовы")
    assert backend.loading is False
    assert states[0] == (True, 0.14) and states[-1] == (False, -1.0)


def test_prefetch_downloads_only_missing_models_smallest_first(monkeypatch):
    from govorilka import models

    downloaded = {"silero"}
    monkeypatch.setattr(models, "is_downloaded", lambda kind, name: name in downloaded)
    assert [label for _k, _n, label in models.missing("gigaam-v3-e2e-rnnt")] == ["модель голосов", "модель распознавания"]
    order, statuses = [], []
    monkeypatch.setattr(models, "_download", lambda kind, name, label, on_status: order.append(kind) or downloaded.add(name))
    assert models.prefetch("gigaam-v3-e2e-rnnt", statuses.append) is True
    assert order == ["se", "asr"] and statuses[-1] == models.DOWNLOADED
    assert models.prefetch("gigaam-v3-e2e-rnnt", statuses.append) is False  # всё уже скачано: ничего не делаем


def test_custom_model_check_reports_a_missing_folder_and_a_bad_address(backend, qapp):
    from conftest import pump

    results = []
    backend.customModelChecked.connect(lambda ok, message, key: results.append((ok, message, key)))
    backend.checkCustomModel("dir", r"E:\такой\папки\нет", "nemo-conformer-ctc", False)
    assert pump(qapp, lambda: len(results) == 1, timeout=20)
    ok, message, key = results[0]
    assert ok is False and key == "" and message  # человек видит причину, настройки не меняются
    backend.checkCustomModel("hf", "просто слово", "", False)
    assert pump(qapp, lambda: len(results) == 2, timeout=20)
    assert results[1][0] is False and "автор/репозиторий" in results[1][1]


def test_model_list_ends_with_the_custom_item_and_labels_custom_keys(backend):
    from govorilka import asr_models

    assert backend.models[-1]["id"] == "__custom__"
    assert {entry["id"] for entry in backend.models[:-1]} >= {"gigaam-v3-e2e-rnnt", "gigaam-v3-e2e-rnnt@int8"}
    assert backend.isCustomModel(asr_models.hf_key("a/b")) and not backend.isCustomModel("gigaam-v3-e2e-ctc")
    assert backend.modelLabel(asr_models.hf_key("a/b")) == "Своя модель: a/b"


def test_manual_update_check_shows_the_new_version_or_says_you_are_up_to_date(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import updates, version

    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append((kind, text)))
    newer = updates.UpdateInfo("9.9.9", "https://github.com/x/releases/9.9.9", "Что нового", "f.exe", "https://example/f.exe", 150_000_000, "ab")
    monkeypatch.setattr(updates, "check", lambda: newer)
    backend.checkForUpdates()
    assert backend.updateStatus == "checking"
    assert pump(qapp, lambda: backend.updateInfo.get("available") is True)
    assert backend.updateInfo["version"] == "9.9.9" and backend.updateStatus == "available" and backend.updateInfo["sizeMb"] == 143
    backend.dismissUpdate()  # «Позже»: плашка скрыта
    assert backend.updateInfo == {"available": False}
    monkeypatch.setattr(updates, "check", lambda: None)
    backend.checkForUpdates()
    assert pump(qapp, lambda: backend.updateStatus == "latest")
    assert ("success", f"У вас последняя версия: {version.VERSION}") in toasts


def test_update_check_failure_is_loud_only_when_you_asked(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import updates

    def fail():
        raise updates.UpdateError("нет связи с GitHub (таймаут)")

    monkeypatch.setattr(updates, "check", fail)
    backend.checkForUpdates()
    assert pump(qapp, lambda: backend.updateStatus == "error") and "нет связи" in backend.updateMessage
    backend.controller.check_for_update(manual=False)  # автопроверка: тихо
    pump(qapp, lambda: False, timeout=0.4)
    assert backend.updateStatus == "error" or backend.updateStatus == ""  # не показывает людям лишнего


def test_skipped_version_is_not_offered_again_by_the_automatic_check(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import controller as controller_module
    from govorilka import updates

    monkeypatch.setattr(controller_module, "save_settings", lambda s: None)
    newer = updates.UpdateInfo("9.9.9", "https://example/p", "", "", "", 0, "")
    monkeypatch.setattr(updates, "check", lambda: newer)
    backend.checkForUpdates()
    assert pump(qapp, lambda: backend.updateStatus == "available")
    backend.skipUpdate()
    assert backend.settings["skipped_update"] == "9.9.9" and backend.updateInfo == {"available": False}
    backend.controller.check_for_update(manual=False)  # автопроверка про пропущенную версию молчит
    pump(qapp, lambda: False, timeout=0.5)
    assert backend.updateInfo == {"available": False}
    backend.checkForUpdates()  # а вручную показываем всё равно
    assert pump(qapp, lambda: backend.updateStatus == "available")


def test_automatic_check_is_off_for_source_runs_and_when_disabled(backend, monkeypatch):
    from govorilka import version

    c = backend.controller
    c._schedule_update_check(1)
    assert c._update_timer is None  # из исходников (dev) автопроверка не запускается
    monkeypatch.setattr(version, "is_release", lambda: True)
    c.settings = c.settings.__class__(check_updates=False)
    c._schedule_update_check(1)
    assert c._update_timer is None
    c.settings = c.settings.__class__(check_updates=True)
    c._schedule_update_check(3600)
    assert c._update_timer is not None
    c._update_timer.cancel()


def test_install_update_prepares_swaps_and_quits_the_app(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import installer, updates

    info = updates.UpdateInfo("9.9.9", "https://github.com/x", "", "f.exe", "https://github.com/f.exe", 100, "ab")
    backend._update = info
    calls = []
    monkeypatch.setattr(installer, "can_install", lambda: True)
    monkeypatch.setattr(installer, "install", lambda i, status: calls.append("install"))
    quits = []
    monkeypatch.setattr(backend, "quit", lambda: quits.append(1))
    backend.installUpdate()
    assert backend.installing
    assert pump(qapp, lambda: quits, timeout=5)
    assert calls == ["install"]


def test_install_update_failure_keeps_the_app_and_explains(backend, qapp, monkeypatch):
    from conftest import pump

    from govorilka import installer, updates

    backend._update = updates.UpdateInfo("9.9.9", "https://github.com/x", "", "f.exe", "https://github.com/f.exe", 100, "ab")
    toasts = []
    backend.toast.connect(lambda text, kind: toasts.append((kind, text)))
    monkeypatch.setattr(installer, "can_install", lambda: True)

    def broken(*a, **k):
        raise installer.InstallError("Контрольная сумма не совпала")

    monkeypatch.setattr(installer, "install", broken)
    quits = []
    monkeypatch.setattr(backend, "quit", lambda: quits.append(1))
    backend.installUpdate()
    assert pump(qapp, lambda: toasts, timeout=5)
    assert not backend.installing and not quits and "Контрольная сумма" in toasts[0][1]


def test_install_update_falls_back_to_browser_when_not_installable(backend, monkeypatch):
    from govorilka import installer, updates

    backend._update = updates.UpdateInfo("9.9.9", "https://github.com/x", "", "f.exe", "https://github.com/f.exe", 100, "ab")
    monkeypatch.setattr(installer, "can_install", lambda: False)
    opened = []
    monkeypatch.setattr(backend, "downloadUpdate", lambda: opened.append(1))
    backend.installUpdate()
    assert opened and not backend.installing


# ---------- выделение текста в ленте ----------


def test_selection_text_is_a_fragment_inside_one_line_and_labelled_lines_across_several():
    model = TranscriptModel()
    model.update([speech("Я", 1, "Первая фраза про сроки"), Utterance("", 3, "Важное", 3, SOURCE_MARK), speech("Собеседник 1", 5, "Вторая фраза")], [])
    assert model.selection_text(0, 7, 0, 12) == "фраза"
    assert model.selection_text(2, 6, 0, 7) == "[00:01] Я: фраза про сроки\n[00:03] ★ Важное\n[00:05] Собеседник 1: Вторая"  # тянули снизу вверх
    assert model.selection_text(0, 0, 9, 10**6).endswith("Собеседник 1: Вторая фраза")  # «до конца» за краем ленты
    assert TranscriptModel().selection_text(0, 0, 1, 1) == ""


@pytest.fixture
def feed(qapp):
    """Настоящая лента TranscriptView в окне: протягиваем мышью и жмём Ctrl+C, как человек."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    from govorilka.config import QML_DIR

    model = TranscriptModel()
    model.update([speech("Я", 1, "Первая фраза про проект и сроки"), speech("Собеседник 1", 5, "Вторая фраза от собеседника")], [])
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("feedModel", model)
    component = QQmlComponent(engine)
    component.setData(
        f'import QtQuick\nimport QtQuick.Window\nimport "{QUrl.fromLocalFile(str(QML_DIR)).toString()}"\n'
        'Window { width: 700; height: 500; visible: true\n'
        '  property var copied: []\n'
        '  TranscriptView { objectName: "feed"; anchors.fill: parent; model: feedModel\n'
        '    onCopyRequested: (a, b, c, d) => copied = [a, b, c, d] } }'.encode(),
        QUrl.fromLocalFile(str(QML_DIR / "feed_test.qml")),
    )
    window = component.create()
    assert window is not None, component.errorString()
    pump(qapp, lambda: False, timeout=0.5)
    yield window, model
    window.close()
    window.deleteLater()


def bodies(window):
    def walk(item):
        yield item
        for child in item.childItems():
            yield from walk(child)

    return [i for i in walk(window.contentItem()) if i.metaObject().className().startswith("QQuickTextEdit")]


def test_dragging_selects_across_replies_and_ctrl_c_copies_them(feed, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    window, model = feed
    first, second = bodies(window)[:2]
    start, end = first.mapToScene(QPointF(60, 8)), second.mapToScene(QPointF(40, 8))
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(start.x()), int(start.y())))
    for step in range(1, 13):
        y = start.y() + (end.y() - start.y()) * step / 12
        x = start.x() + (end.x() - start.x()) * step / 12
        QTest.mouseMove(window, QPoint(int(x), int(y)), 10)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(end.x()), int(end.y())))
    pump(qapp, lambda: False, timeout=0.1)
    assert first.property("selectedText") and second.property("selectedText")  # подсвечены обе реплики
    QTest.keyClick(window, Qt.Key_C, Qt.ControlModifier)  # на Mac Qt называет так ⌘
    assert pump(qapp, lambda: bool(window.property("copied").toVariant()))
    text = model.selection_text(*[int(v) for v in window.property("copied").toVariant()])
    assert text.startswith("[00:01] Я: ") and "\n[00:05] Собеседник 1: Вт" in text


def test_click_clears_the_selection_and_ctrl_a_selects_everything(feed, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    window, _ = feed
    first, second = bodies(window)[:2]
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(first.mapToScene(QPointF(10, 8)).x()), int(first.mapToScene(QPointF(10, 8)).y())))
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)  # на Mac Qt называет так ⌘
    pump(qapp, lambda: False, timeout=0.1)
    assert first.property("selectedText") == first.property("text") and second.property("selectedText") == second.property("text")
    point = first.mapToScene(QPointF(30, 8))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(point.x()), int(point.y())))
    pump(qapp, lambda: False, timeout=0.1)
    assert not first.property("selectedText") and not second.property("selectedText")
