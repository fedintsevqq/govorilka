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


LIVE_SKIP = ("Пауза", "Отметка", "Расшифровать", "Выйти", "Сохранить как", "Открыть файл", "Открыть папку", "Открыть страницу", "Скачать",
             "Установить", "Отправить", "Скопировать для", "Удалить", "Сбросить", "Изменить…", "Выбрать папку")


def _live_check(app: QApplication, root: QQuickWindow, backend: Backend) -> None:
    """Самопроверка в настоящем окне (--selftest-live): нажимаем все безопасные кнопки главного окна и настроек,
    потом даём поработать фоновым потокам. Упадёт — CI покажет crash.log с трассировкой потоков."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from govorilka.config import DATA_DIR

    report = DATA_DIR / "selftest-live.txt"
    clicked: list[str] = []

    def buttons(window: QQuickWindow) -> list:
        found = []

        def walk(item) -> None:  # noqa: ANN001
            for child in item.childItems():
                name = child.metaObject().className()
                if "RecordButton" in name:  # запись на сервере сборки запросила бы микрофон и ждала ответа
                    walk(child)
                    continue
                if child.isVisible() and ("Button" in name or "ChipToggle" in name) and child.isEnabled():
                    label = str(child.property("text") or child.property("tip") or child.property("iconName") or name)
                    if not any(word in label for word in LIVE_SKIP) and child.width() > 4:
                        found.append((label, child))
                walk(child)

        walk(window.contentItem())
        return found

    def click_all(window: QQuickWindow, where: str) -> None:
        for label, item in buttons(window)[:40]:
            centre = item.mapToScene(item.boundingRect().center())
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             QPoint(int(centre.x()), int(centre.y())))
            clicked.append(f"{where}: {label}")
            for _ in range(10):
                app.processEvents()
            QTest.keyClick(window, Qt.Key.Key_Escape)  # закрыть открывшееся меню или окно
            for _ in range(5):
                app.processEvents()

    def step() -> None:
        backend.finishTour()
        click_all(root, "окно")
        settings = root.findChild(QObject, "settings")
        if settings is not None:
            settings.open()
            for _ in range(20):
                app.processEvents()
            click_all(settings, "настройки")
            settings.close()
        report.write_text("OK\n" + "\n".join(clicked) + "\n", encoding="utf-8")
        log.info("Самопроверка в окне: нажато кнопок — %d", len(clicked))

    QTimer.singleShot(3000, step)
    QTimer.singleShot(20000, app.quit)


def run(background: bool = False, updated: bool = False, live_check: bool = False) -> int:
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
        installer.cleanup_after_update()
    QTimer.singleShot(STARTUP_NOTICE_DELAY_MS, backend.flush_startup_notices)
    QTimer.singleShot(STARTUP_NOTICE_DELAY_MS, backend.controller.prefetch_models)  # первый запуск: модели качаются сразу
    if live_check:
        _live_check(app, root, backend)
    app.aboutToQuit.connect(lambda: (backend.shutdown(), tray.hide() if tray else None, lock.close()))
    code = app.exec()
    del engine
    return code
