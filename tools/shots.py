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

from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer
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


def shoot(backend: Backend, window, path: Path) -> None:
    image = window.grabWindow()
    image.save(str(path))
    print("снимок:", path)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
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
        (3, {}, "0-tour-4"),
    ]:
        steps.append(lambda step=step, state=state: tour(step, **state))
        steps.append(lambda name=name: shoot(backend, root, out / f"{name}.png"))
    steps.append(lambda: invoke("tour", "close"))
    steps.append(lambda: shoot(backend, root, out / "1-idle.png"))
    steps.append(lambda: (populate(True), None)[1])
    steps.append(lambda: shoot(backend, root, out / "2-recording.png"))
    steps.append(lambda: invoke("settings", "open"))
    steps.append(lambda: shoot(backend, root.findChild(QObject, "settings"), out / "3-settings.png"))
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
