"""Значок в строке меню macOS и в трее Windows.

Состояния: ожидание, запись (красный), пауза (янтарный). Меню собирается заново при каждом открытии,
поэтому пункты всегда соответствуют состоянию: «Начать запись» или «Остановить», «Пауза» или «Продолжить».
На Windows щелчок по значку показывает окно, на macOS щелчок открывает меню (так принято в строке меню).
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from govorilka.config import ASSETS_DIR
from govorilka.qt.backend import Backend

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"


def tray_icon(state: str) -> QIcon:
    icon = _tray_icon(state)
    if icon.isNull():
        log.warning("Нет файла значка для трея (%s): папка %s", state, ASSETS_DIR)
    return icon


def _tray_icon(state: str) -> QIcon:
    """Иконка под состояние. На Mac ожидание — «шаблон»: система сама красит её под тему строки меню."""
    if state in ("recording", "paused"):
        return QIcon(str(ASSETS_DIR / f"tray-{state}-disc.png"))
    if IS_MAC:
        icon = QIcon(str(ASSETS_DIR / "tray-idle-template.png"))
        icon.setIsMask(True)
        return icon
    return QIcon(str(ASSETS_DIR / "icon.png"))  # цветной знак виден и на светлой, и на тёмной панели задач


class Tray(QObject):
    def __init__(self, backend: Backend, show_window, open_settings, quit_app, parent: QObject | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._backend = backend
        self._show_window, self._open_settings, self._quit = show_window, open_settings, quit_app
        self._icon = QSystemTrayIcon(tray_icon("idle"), self)
        self._icon.setToolTip("Говорилка")
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._rebuild)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._hint_shown = False
        backend.stateChanged.connect(self._on_state)
        backend.hiddenToTray.connect(self._explain_once)
        backend.elapsedChanged.connect(self._update_tooltip)
        self._rebuild()

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    # ---------- меню ----------

    def _action(self, text: str, slot, enabled: bool = True) -> QAction:  # noqa: ANN001
        action = QAction(text, self._menu)
        action.setEnabled(enabled)
        action.triggered.connect(lambda _checked=False: slot())
        self._menu.addAction(action)
        return action

    def _rebuild(self) -> None:
        state = self._backend.state
        live = state in ("recording", "paused")
        idle = state == "idle"
        self._menu.clear()
        self._action("Остановить запись" if live else "Начать запись", lambda: self._backend.toggleRecording(""), idle or live)
        if live:
            self._action("Продолжить" if state == "paused" else "Пауза", self._backend.togglePause)
            self._action("Поставить отметку ★", self._backend.addMark)
        self._menu.addSeparator()
        self._action("Отправить в Claude Code", self._backend.sendToClaude)
        self._action("Открыть транскрипт", self._backend.openTranscript)
        self._action("Папка с транскриптами", self._backend.openFolder)
        self._menu.addSeparator()
        self._action("Показать окно", self._show_window)
        self._action("Настройки…", self._open_settings)
        self._menu.addSeparator()
        self._action("Выйти из Говорилки", self._quit)

    # ---------- состояние ----------

    def _on_state(self) -> None:
        state = self._backend.state
        self._icon.setIcon(tray_icon(state))
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        state = self._backend.state
        label = {"recording": "Запись", "paused": "Пауза"}.get(state)
        self._icon.setToolTip(f"Говорилка — {label} {self._backend.elapsed}" if label else "Говорилка")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if not IS_MAC and reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _explain_once(self) -> None:
        """Первый раз, когда окно ушло в трей: объясняем, что программа не закрылась."""
        if self._hint_shown:
            return
        self._hint_shown = True
        where = "значок в строке меню" if IS_MAC else "значок возле часов"
        self._icon.showMessage(
            "Говорилка осталась в фоне",
            f"Запись и горячая клавиша работают. Чтобы выйти совсем — {where} → «Выйти из Говорилки».",
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )
