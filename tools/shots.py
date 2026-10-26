"""Снимки интерфейса для проверки глазами: python tools/shots.py <папка>. Нужен экран (обычный запуск на ПК)."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# снимки не должны трогать настоящие настройки и память голосов
os.environ.setdefault("GOVORILKA_DATA_DIR", tempfile.mkdtemp(prefix="govorilka-shots-"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QTimer
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from govorilka.config import Settings
from govorilka.controller import AppState, Controller
from govorilka.pipeline import TextEvent
from govorilka.qt.app import create_engine
from govorilka.qt.backend import Backend
from govorilka.transcript import SOURCE_MARK, SOURCE_NOTE, Transcript, Utterance


class _NoHotkeys:
    def set_chord(self, chord): ...
    def start(self): ...
    def stop(self): ...


DIALOG = [
    ("Собеседник 1", 12, "Добрый день, коллеги. Начинаем планёрку по релизу."),
    ("Собеседник 1", 17, "Сегодня обсуждаем регрессионное тестирование и план на пятницу."),
    ("Я", 24, "Привет! Я возьму смоук-тесты на себя и отпишусь в чат до обеда."),
    ("Собеседник 2", 31, "Отлично. А что с оплатой картой? Там падал сценарий с 3-D Secure."),
    ("Собеседник 2", 38, "Нужно перепроверить на стенде, у нас два бага ещё открыты."),
    ("MARK", 44, "Баги по оплате"),
    ("Я", 47, "Заведу карточки и приложу логи, посмотрим до пятницы."),
    ("NOTE", 60, "Пауза"),
    ("Собеседник 1", 75, "Продолжаем. Кто берёт регресс по входу через телефон?"),
]


SUMMARY = """### Кратко
Планёрка по релизу: распределили регрессионное тестирование, обсудили баги оплаты картой.

### Решения
- Регресс начинаем в пятницу.

### Задачи
- **Я** — смоук-тесты, отписаться в чат до обеда.
- **Я** — завести карточки по багам оплаты и приложить логи до пятницы.
- **Собеседник 2** — перепроверить 3-D Secure на стенде.

### Открытые вопросы
- Кто берёт регресс по входу через телефон?
"""


def shoot(backend: Backend, window, path: Path) -> None:
    image = window.grabWindow()
    image.save(str(path))
    print("снимок:", path)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
    from govorilka import installer

    installer.can_install = lambda: True  # снимки из исходников показывают кнопку как у собранного приложения
    app = QApplication(sys.argv)
    backend = Backend(lambda l: Controller(l, Settings(theme=sys.argv[2] if len(sys.argv) > 2 else "dark"), hotkeys=_NoHotkeys()))
    engine = create_engine(backend)
    root = engine.rootObjects()[0]
    width, height = (int(v) for v in (sys.argv[3] if len(sys.argv) > 3 else "1120x740").split("x"))
    root.setWidth(width)
    root.setHeight(height)
    steps = []

    def populate(recording: bool) -> None:
        transcript = Transcript(datetime(2026, 10, 23, 14, 5), "Планёрка по релизу")
        for who, at, text in DIALOG:
            if who == "MARK":
                transcript.add(Utterance("", at, text, at, SOURCE_MARK))
            elif who == "NOTE":
                transcript.add(Utterance("", at, text, at, SOURCE_NOTE))
            else:
                transcript.add(Utterance(who, at, text, at + 4, "mic" if who == "Я" else "system"))
        backend._on_transcript(transcript, Path("2026-10-23_14-05_Планёрка_по_релизу.md"))
        backend.controller.last_file = Path("2026-10-23_14-05_Планёрка_по_релизу.md")
        backend.transcriptInfoChanged.emit()
        if recording:
            backend._partials["system"] = TextEvent("system", "Собеседник 2", "partial", "Давайте тогда договоримся, что регресс начинаем", 84, 90)
        backend._refresh()
        backend.controller.state = AppState.RECORDING if recording else AppState.IDLE
        backend.stateChanged.emit()
        backend._shown_levels.update({"mic": 0.55, "system": 0.85})
        backend._level_timer.stop()  # для снимка уровень держим постоянным
        backend.levelsChanged.emit()
        backend._elapsed = "01:27"
        backend.elapsedChanged.emit()

    def invoke(name: str, method: str) -> None:
        obj = root.findChild(QObject, name)
        QMetaObject.invokeMethod(obj, method, Qt.ConnectionType.DirectConnection)

    def tour(step: int, **state: object) -> None:
        popup = root.findChild(QObject, "tour")
        popup.setProperty("step", step)
        for key, value in state.items():
            popup.setProperty(key, value)

    # знакомство открывается само: в настройках оно ещё не пройдено
    for step, state, name in [
        (0, {}, "0-tour-1"),
        (1, {}, "0-tour-2"),
        (1, {"menuSeen": True, "chosen": "record"}, "0-tour-2-done"),
        (2, {}, "0-tour-3"),
        (3, {}, "0-tour-dictation"),
        (4, {}, "0-tour-4"),
    ]:
        steps.append(lambda step=step, state=state: tour(step, **state))
        steps.append(lambda name=name: shoot(backend, root, out / f"{name}.png"))
    steps.append(lambda: invoke("tour", "close"))
    steps.append(lambda: shoot(backend, root, out / "1-idle.png"))
    steps.append(lambda: invoke("hintsPopup", "open"))
    steps.append(lambda: shoot(backend, root, out / "1-hints.png"))
    steps.append(lambda: invoke("hintsPopup", "close"))
    steps.append(lambda: invoke("aiMenu", "open"))
    steps.append(lambda: shoot(backend, root, out / "1-ai-menu.png"))
    steps.append(lambda: invoke("aiMenu", "close"))
    steps.append(lambda: (populate(True), None)[1])
    steps.append(lambda: shoot(backend, root, out / "2-recording.png"))
    def select_and_menu() -> None:
        feed = root.findChild(QObject, "feed")
        for key, value in {"aRow": 1, "aPos": 9, "bRow": 3, "bPos": 25}.items():
            feed.setProperty(key, value)
        QMetaObject.invokeMethod(feed, "openMenu", Qt.ConnectionType.DirectConnection, Q_ARG("QVariant", 520), Q_ARG("QVariant", 300))

    def offer_update(installing: bool) -> None:
        from govorilka import updates

        info = updates.UpdateInfo("0.3.3", "https://github.com/fedintsevqq/govorilka/releases/tag/v0.3.3", "", "f.exe", "https://github.com/f.exe", 176_000_000, "ab")
        backend._on_update(info, "", False)
        backend._installing = installing
        if installing:
            backend._set_status("Скачиваю обновление: 61 из 168 МБ (36%)")
        backend.updateChanged.emit()

    import math
    import random

    voice = QTimer()
    voice.setInterval(100)
    tick_count = [0]

    def voice_tick() -> None:   # «говорим»: слоги с паузами, как настоящая речь
        tick_count[0] += 1
        syllable = max(0.0, math.sin(tick_count[0] * 0.9)) * (0.55 + 0.45 * random.random())
        backend._dictation_level = min(1.0, 0.1 + syllable)
        backend.dictationLevelChanged.emit()

    voice.timeout.connect(voice_tick)

    def dictation(phase: str, level: float = 0.0, note: str = "") -> None:
        backend._on_dictation(phase)
        if phase == "listening" and level > 0.5:
            voice.start()
        else:
            voice.stop()
            backend._dictation_level = level
            backend.dictationLevelChanged.emit()
        backend._dictation_note = note
        backend.dictationChanged.emit()

    pill = lambda: root.findChild(QObject, "dictationPill") or next(  # noqa: E731
        w for w in app.topLevelWindows() if w.objectName() == "dictationPill"
    )
    for phase, level, note, name in [
        ("listening", 0.15, "", "8-dictation-quiet"),
        ("listening", 0.8, "", "8-dictation-speaking"),
        ("recognizing", 0.0, "", "8-dictation-recognizing"),
        ("", 0.0, "Текст в буфере: нажмите ⌘V. Чтобы вставлялось само, включите «Универсальный доступ»", "8-dictation-note"),
    ]:
        steps.append(lambda phase=phase, level=level, note=note: dictation(phase, level, note))
        steps.append(lambda name=name: shoot(backend, pill(), out / f"{name}.png"))
    steps.append(lambda: dictation("", 0.0, ""))
    def assist(phase: str) -> None:
        from dataclasses import replace as _replace

        backend.controller.settings = _replace(backend.controller.settings, assistant_enabled=True, assistant_source="api")
        backend.settingsChanged.emit()
        backend._on_assistant("ready", "")
        if phase in ("thinking", "answer"):
            backend._on_assistant("question", "А как вы будете проверять оплату с 3-D Secure до пятницы?")
        if phase == "answer":
            backend._on_assistant("done", "Прогоню сценарий на стенде с тестовыми картами: успешная оплата, отказ банка и таймаут. "
                                          "Баги заведу с логами сегодня, к пятнице будет понятно, закрываем ли до релиза.")
        backend._assist_protection = "hidden"
        backend.assistantChanged.emit()

    panel = lambda: next(w for w in app.topLevelWindows() if w.objectName() == "assistantPanel")  # noqa: E731
    for phase in ("thinking", "answer"):
        steps.append(lambda phase=phase: assist(phase))
        steps.append(lambda phase=phase: shoot(backend, panel(), out / f"12-assistant-{phase}.png"))
    steps.append(lambda: invoke("settings", "open"))
    steps.append(lambda: invoke("settings", "showAssistant"))
    steps.append(lambda: shoot(backend, root.findChild(QObject, "settings"), out / "3-settings-assistant.png"))
    steps.append(lambda: root.findChild(QObject, "settings").close())
    steps.append(lambda: backend._on_assistant("off", ""))
    steps.append(lambda: offer_update(False))
    steps.append(lambda: shoot(backend, root, out / "6-update.png"))
    steps.append(lambda: offer_update(True))
    steps.append(lambda: shoot(backend, root, out / "7-update-installing.png"))
    steps.append(lambda: (setattr(backend, "_installing", False), backend.updateChanged.emit()))
    def summary_state(state: str) -> None:
        root.setProperty("panel", "summary" if state else "feed")
        backend._transcript.summary = SUMMARY if state == "done" else ""
        backend._summary_busy = state == "busy"
        backend._summary_status = "Читаю разговор: часть 2 из 3…" if state == "busy" else ""
        backend.summaryChanged.emit()
        backend.summaryProgress.emit()

    for state in ("empty", "busy", "done"):
        steps.append(lambda state=state: summary_state(state))
        steps.append(lambda state=state: shoot(backend, root, out / f"9-summary-{state}.png"))
    steps.append(lambda: summary_state(""))
    def guide(mode: str) -> None:
        backend._whats_new_since = "0.3.2"
        backend.settingsChanged.emit()
        QMetaObject.invokeMethod(root.findChild(QObject, "featureGuide"), "show", Qt.ConnectionType.DirectConnection, Q_ARG("QVariant", mode))

    for mode in ("new", "all"):
        steps.append(lambda mode=mode: guide(mode))
        steps.append(lambda mode=mode: shoot(backend, root, out / f"10-guide-{mode}.png"))
        steps.append(lambda: invoke("featureGuide", "close"))
    def install_error() -> None:
        offer_update(False)
        backend._install_error = "Говорилка запущена прямо из образа или карантина: перетащите её в «Программы» и откройте оттуда"
        backend.updateChanged.emit()

    steps.append(install_error)
    steps.append(lambda: shoot(backend, root, out / "11-update-error.png"))
    steps.append(lambda: (setattr(backend, "_install_error", ""), backend.updateChanged.emit()))
    steps.append(select_and_menu)
    steps.append(lambda: shoot(backend, root, out / "5-selection.png"))
    steps.append(lambda: invoke("feed", "clearSelection"))
    steps.append(lambda: invoke("settings", "open"))
    steps.append(lambda: shoot(backend, root.findChild(QObject, "settings"), out / "3-settings.png"))
    steps.append(lambda: invoke("settings", "showCatalog"))
    steps.append(lambda: shoot(backend, root.findChild(QObject, "settings"), out / "3-settings-catalog.png"))
    steps.append(lambda: (offer_update(False), invoke("settings", "showUpdates")))
    steps.append(lambda: shoot(backend, root.findChild(QObject, "settings"), out / "3-settings-updates.png"))
    steps.append(lambda: (root.findChild(QObject, "settings").close(), invoke("radial", "dismiss"))[1])
    steps.append(lambda: backend.radialRequested.emit(900, 400))
    steps.append(lambda: None)
    steps.append(lambda: None)
    steps.append(lambda: shoot(backend, root.findChild(QObject, "radial"), out / "4-radial.png"))
    steps.append(lambda: app.quit())

    def tick() -> None:
        if steps:
            steps.pop(0)()
            QTimer.singleShot(650, tick)

    QTimer.singleShot(1200, tick)
    app.exec()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
