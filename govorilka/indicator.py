"""Маленькая плашка поверх окон: «● 12:34» во время записи и короткие сообщения.

Нужна для тихого режима: окно свёрнуто, а по плашке видно, что запись идёт и когда она закончилась.
Плашка не забирает фокус у программы, в которой вы работаете.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk

log = logging.getLogger(__name__)

RECORDING = "#ff5c5c"
NEUTRAL = "#d0d0d0"
OK = "#4cc38a"
BACKGROUND = "#1e1f22"
MARGIN = 24


class RecordingIndicator:
    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._window: tk.Toplevel | None = None
        self._dot: tk.Label | None = None
        self._label: tk.Label | None = None
        self._hide_job: str | None = None

    def show(self, text: str, color: str = NEUTRAL, ttl_ms: int | None = None) -> None:
        """Показывает плашку с текстом. ttl_ms — через сколько миллисекунд убрать (None — держать)."""
        try:
            self._ensure_window()
            assert self._dot is not None and self._label is not None and self._window is not None
            self._dot.configure(fg=color)
            self._label.configure(text=text)
            self._place()
            self._window.deiconify()
            if self._hide_job is not None:
                self._root.after_cancel(self._hide_job)
                self._hide_job = None
            if ttl_ms is not None:
                self._hide_job = self._root.after(ttl_ms, self.hide)
        except tk.TclError:
            log.exception("Не получилось показать плашку записи")

    def update_text(self, text: str) -> None:
        """Меняет текст, не трогая таймер скрытия: так обновляется время записи."""
        if self._label is not None and self._window is not None and self._window.winfo_viewable():
            self._label.configure(text=text)

    def hide(self) -> None:
        self._hide_job = None
        if self._window is not None:
            self._window.withdraw()

    # ---------- внутреннее ----------

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        window = tk.Toplevel(self._root)
        window.overrideredirect(True)  # без рамки и заголовка
        window.attributes("-topmost", True)
        try:
            window.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        window.configure(bg=BACKGROUND, padx=12, pady=6)
        self._dot = tk.Label(window, text="●", bg=BACKGROUND, fg=NEUTRAL, font=("Segoe UI", 12))
        self._dot.pack(side="left")
        self._label = tk.Label(window, text="", bg=BACKGROUND, fg="#f0f0f0", font=("Segoe UI", 11))
        self._label.pack(side="left", padx=(8, 0))
        self._window = window
        self._make_non_activating()

    def _make_non_activating(self) -> None:
        """На Windows помечаем окно как «не активируется», иначе оно отнимет ввод у вашей программы."""
        if sys.platform != "win32" or self._window is None:
            return
        try:
            import ctypes

            self._window.update_idletasks()
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetParent(self._window.winfo_id()) or self._window.winfo_id()
            style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x80)  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        except Exception:  # noqa: BLE001 — плашка работает и без этого, просто может перехватить фокус
            log.exception("Не удалось сделать плашку неактивируемой")

    def _place(self) -> None:
        if self._window is None:
            return
        self._window.update_idletasks()
        width = self._window.winfo_reqwidth()
        x = self._window.winfo_screenwidth() - width - MARGIN
        self._window.geometry(f"+{x}+{MARGIN * 2}")
