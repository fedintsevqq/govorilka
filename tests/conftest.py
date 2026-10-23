"""Общие настройки тестов: Qt работает без экрана и без видеокарты (программная отрисовка)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication(["govorilka-tests"])
    yield app


def pump(app, condition, timeout: float = 3.0) -> bool:
    """Крутит цикл событий Qt, пока условие не выполнится: сигналы из потоков приходят именно через него."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()
