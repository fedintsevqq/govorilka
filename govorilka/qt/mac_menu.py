"""Строка меню macOS: «Говорилка», «Запись», «Окно», «Справка» — по-русски, с привычными сочетаниями клавиш.

Без неё у программы было только меню Qt по умолчанию с одним пунктом «Quit Govorilka» по-английски.
Меню без родителя Qt на Mac делает общей строкой меню программы.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenuBar


def build(
    show_window: Callable[[], None],
    hide_window: Callable[[], None],
    settings: Callable[[], None],
    toggle_recording: Callable[[], None],
    add_mark: Callable[[], None],
    features: Callable[[], None],
    tour: Callable[[], None],
    quit_app: Callable[[], None],
) -> QMenuBar:
    bar = QMenuBar()

    def action(menu, text: str, slot: Callable[[], None], keys: str = "", role=QAction.MenuRole.NoRole) -> QAction:  # noqa: ANN001
        item = QAction(text, bar)
        if keys:
            item.setShortcut(QKeySequence(keys))
        item.setMenuRole(role)
        item.triggered.connect(lambda _checked=False: slot())
        menu.addAction(item)
        return item

    app_menu = bar.addMenu("Говорилка")
    # роли переносят пункты в меню с именем программы, как у всех программ Mac
    action(app_menu, "Настройки…", settings, "Ctrl+,", QAction.MenuRole.PreferencesRole)
    action(app_menu, "Завершить Говорилку", quit_app, "Ctrl+Q", QAction.MenuRole.QuitRole)

    record = bar.addMenu("Запись")
    action(record, "Начать или остановить запись", toggle_recording, "Ctrl+R")
    action(record, "Поставить отметку", add_mark, "Ctrl+Shift+M")

    window = bar.addMenu("Окно")
    action(window, "Показать окно Говорилки", show_window, "Ctrl+0")
    action(window, "Спрятать в строку меню", hide_window, "Ctrl+W")

    help_menu = bar.addMenu("Справка")
    action(help_menu, "Как пользоваться Говорилкой", features)
    action(help_menu, "Пройти знакомство заново", tour)
    return bar
