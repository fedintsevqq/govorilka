"""Запуск Qt-приложения: окно QML, трей, один экземпляр, фоновый режим."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QtMsgType, QTimer, QUrl, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from govorilka import installer, single_instance, version
from govorilka.config import ASSETS_DIR, QML_DIR
from govorilka.qt.backend import Backend
from govorilka.qt.icons import IconProvider
from govorilka.qt.tray import Tray

log = logging.getLogger(__name__)

STARTUP_NOTICE_DELAY_MS = 900  # даём окну нарисоваться, потом показываем сообщения при запуске


class QmlLoadError(RuntimeError):
    pass


def _log_qt_message(kind: QtMsgType, _context: object, message: str) -> None:
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        log.warning("Qt: %s", message)


class _QuitFilter(QObject):
    """Ловит запрос на выход из приложения (Cmd+Q) раньше, чем окна успеют его отменить."""

    def __init__(self, backend: Backend) -> None:
        super().__init__(backend)
        self._backend = backend

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Quit:
            self._backend.begin_quit()
        return False


def create_engine(backend: Backend, strict: bool = False) -> QQmlApplicationEngine:
    """Загружает главное окно QML. Если окна нет, это исключение: пустое окно хуже явного сбоя.

    strict — любое предупреждение QML тоже ошибка (так проверяют тесты и самопроверка сборки). В обычном запуске
    безобидное предупреждение не должно мешать работе, поэтому оно только пишется в журнал."""
    # системная отрисовка текста (ClearType на Windows, хинтинг на Mac): буквы чёткие, как в родных
    # приложениях. Встроенная в Qt Quick (по полям расстояний) на обычных экранах выглядит мыльной.
    QQuickWindow.setTextRenderType(QQuickWindow.TextRenderType.NativeTextRendering)
    engine = QQmlApplicationEngine()
    problems: list[str] = []
    engine.warnings.connect(lambda warnings: problems.extend(w.toString() for w in warnings))
    engine.warnings.connect(lambda warnings: [log.warning("QML: %s", w.toString()) for w in warnings])
    engine.addImportPath(str(QML_DIR))
    engine.addImageProvider("icon", IconProvider())
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        raise QmlLoadError("Не загрузилось окно: " + "; ".join(problems))
    if problems and strict:
        raise QmlLoadError("QML сообщил об ошибках: " + "; ".join(problems))
    return engine


def run(background: bool = False, updated: bool = False) -> int:
    holder: list[Backend] = []
    lock = single_instance.acquire(
        lambda: holder[0].windowRequested.emit() if holder else None, notify_existing=not background
    )
    if lock is None:
        return 0  # уже запущена другая копия, ей отправлено «покажись»

    QQuickStyle.setStyle("Basic")  # стили Windows/macOS перекрасили бы наши кнопки и поля
    app = QApplication(sys.argv)
    app.setApplicationName("Говорилка")
    app.setOrganizationName("Govorilka")
    app.setWindowIcon(QIcon(str(ASSETS_DIR / "icon.png")))
    app.setQuitOnLastWindowClosed(False)  # крестик убирает окно в трей, а не завершает программу

    # ошибки привязок QML появляются уже после загрузки и без консоли исчезали бы: пишем их в журнал
    qInstallMessageHandler(_log_qt_message)
    backend = Backend(parent=app)  # удаляется вместе с приложением, а не раньше движка
    holder.append(backend)
    try:
        engine = create_engine(backend)
    except QmlLoadError:
        log.exception("Интерфейс не загрузился")
        lock.close()
        raise

    root = engine.rootObjects()[0]
    tray = None
    if Tray.available():
        tray = Tray(backend, backend.windowRequested.emit, backend.settingsRequested.emit, backend.quit)
        tray.show()
        backend.set_tray_available(True)
    backend.quitRequested.connect(app.quit)
    if background and tray is not None:
        root.hide()  # автозапуск: окно не выскакивает, работают трей и горячая клавиша
    if sys.platform == "darwin":
        # Cmd+Q и «Завершить» в Dock приходят как событие выхода: пока окно «закрывается в трей», оно бы его отменило
        quit_filter = _QuitFilter(backend)
        app.installEventFilter(quit_filter)
        app._quit_filter = quit_filter  # type: ignore[attr-defined]  # держим ссылку: иначе фильтр соберёт сборщик мусора

        # щелчок по значку в Dock, когда окно спрятано, — показать окно
        def on_state(state: Qt.ApplicationState) -> None:
            if state == Qt.ApplicationState.ApplicationActive and not root.isVisible():
                backend.windowRequested.emit()

        app.applicationStateChanged.connect(on_state)

    backend.start()
    if updated:  # запущены установщиком обновления: новая версия поднялась, прежнюю копию можно удалить
        backend._startup_notices.append(f"Готово: Говорилка обновлена до версии {version.VERSION}")  # noqa: SLF001
        if installer.can_install():
            installer.remove_backup(Path(sys.executable))
    QTimer.singleShot(STARTUP_NOTICE_DELAY_MS, backend.flush_startup_notices)
    QTimer.singleShot(STARTUP_NOTICE_DELAY_MS, backend.controller.prefetch_models)  # первый запуск: модели качаются сразу
    app.aboutToQuit.connect(lambda: (backend.shutdown(), tray.hide() if tray else None, lock.close()))
    code = app.exec()
    del engine
    return code
