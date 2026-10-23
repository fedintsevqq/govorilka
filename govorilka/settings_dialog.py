"""Окно настроек: модель, папки, горячая клавиша, автозапуск, запрос для Claude Code."""

from __future__ import annotations

import subprocess
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from tkinter import filedialog, messagebox, ttk

from govorilka.config import MODELS, Settings
from govorilka.hotkeys import (
    HOLD_S,
    INPUT_MONITORING_URL,
    IS_MAC,
    Chord,
    ChordRecorder,
    Keyboard,
    chord_to_string,
    conflicts_with_language_switch,
    format_chord,
    format_snapshot,
    input_monitoring_status,
    parse_chord,
    request_input_monitoring,
    system_keyboard,
    windows_language_switch,
)
from govorilka.transcript_view import FONT, FONT_SIZE

CAPTURE_TIMEOUT_S = 10.0
CAPTURE_POLL_MS = 40
LIVE_POLL_MS = 100


class SettingsDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        settings: Settings,
        autostart_enabled: bool,
        on_save: Callable[[Settings, bool], None],
        on_capture: Callable[[bool], None] = lambda _: None,
    ) -> None:
        super().__init__(parent)
        self.title("Настройки")
        self.geometry("680x640")
        self.transient(parent)
        self._settings = settings
        self._on_save = on_save
        self._on_capture = on_capture  # True — начали записывать комбинацию (фоновую клавишу на паузу), False — закончили
        self._keyboard: Keyboard | None = None
        self._chord = self._load_chord(settings.hotkey)
        self._recorder: ChordRecorder | None = None
        self._deadline = 0.0
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        row = 0
        ttk.Label(frame, text="Модель распознавания:").grid(row=row, column=0, columnspan=2, sticky="w")
        self.model_var = tk.StringVar(value=settings.model)
        ttk.Combobox(frame, textvariable=self.model_var, values=list(MODELS), state="readonly").grid(
            row=row + 1, column=0, sticky="w", pady=(2, 0)
        )

        self.dir_var = tk.StringVar(value=settings.transcripts_dir)
        self.claude_var = tk.StringVar(value=settings.claude_workdir)
        row = self._path_row(frame, "Папка для транскриптов:", self.dir_var, row + 2)
        row = self._path_row(frame, "Где запускать Claude Code (пусто — в папке транскриптов):", self.claude_var, row)

        ttk.Separator(frame).grid(row=row, column=0, columnspan=2, sticky="ew", pady=12)
        row += 1
        self.hotkey_enabled_var = tk.BooleanVar(value=settings.hotkey_enabled)
        ttk.Checkbutton(
            frame,
            text="Начинать и останавливать запись по горячей клавише (работает при свёрнутом окне)",
            variable=self.hotkey_enabled_var,
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        self.hotkey_text = tk.StringVar(value=format_chord(self._chord))
        ttk.Entry(frame, textvariable=self.hotkey_text, state="readonly").grid(
            row=row + 1, column=0, sticky="ew", pady=(6, 0)
        )
        self.hotkey_btn = ttk.Button(frame, text="Изменить…", command=self._start_capture)
        self.hotkey_btn.grid(row=row + 1, column=1, padx=4, pady=(6, 0))
        self.hotkey_hint = tk.StringVar(
            value=f"Только из модификаторов: удерживайте {HOLD_S:g} с. С буквой (Ctrl+Shift+R): достаточно нажать."
        )
        ttk.Label(frame, textvariable=self.hotkey_hint, foreground="gray").grid(
            row=row + 2, column=0, columnspan=2, sticky="w"
        )
        self.live_keys = tk.StringVar(value="Сейчас нажато: …")
        ttk.Label(frame, textvariable=self.live_keys).grid(row=row + 3, column=0, columnspan=2, sticky="w")
        row += 4
        if input_monitoring_status() is False:
            # без «Мониторинга ввода» macOS не отдаёт состояние клавиш: комбинацию не записать и в фоне она не сработает
            ttk.Label(
                frame,
                text="macOS не даёт Говорилке видеть клавиши. Включите её в «Мониторинге ввода» и перезапустите.",
                foreground="#cf222e",
                wraplength=560,
            ).grid(row=row, column=0, sticky="w", pady=(4, 0))
            ttk.Button(frame, text="Открыть", command=self._open_input_monitoring).grid(row=row, column=1, padx=4)
            row += 1

        self.autostart_var = tk.BooleanVar(value=autostart_enabled)
        ttk.Checkbutton(frame, text="Запускать Говорилку при входе в систему (свёрнутой)", variable=self.autostart_var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        self.indicator_var = tk.BooleanVar(value=settings.show_indicator)
        ttk.Checkbutton(
            frame, text="Показывать плашку «● Запись» поверх окон", variable=self.indicator_var
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w")
        row += 2

        ttk.Separator(frame).grid(row=row, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Label(frame, text="Запрос для Claude Code ({file} — путь к транскрипту):").grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 2)
        )
        self.prompt = tk.Text(frame, height=6, wrap="word", font=(FONT, FONT_SIZE - 1))
        self.prompt.insert("1.0", settings.claude_prompt)
        self.prompt.grid(row=row + 2, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(row + 2, weight=1)

        buttons = ttk.Frame(frame)
        buttons.grid(row=row + 3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right", padx=6)
        self.after(LIVE_POLL_MS, self._poll_live_keys)

    @staticmethod
    def _load_chord(text: str) -> Chord:
        try:
            return parse_chord(text)
        except ValueError:
            from govorilka.hotkeys import DEFAULT_HOTKEY

            return parse_chord(DEFAULT_HOTKEY)

    def _path_row(self, frame: ttk.Frame, label: str, var: tk.StringVar, row: int) -> int:
        ttk.Label(frame, text=label).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Entry(frame, textvariable=var).grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(frame, text="…", width=3, command=lambda: self._browse(var)).grid(row=row + 1, column=1, padx=4)
        return row + 2

    def _browse(self, var: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=var.get() or None)
        if chosen:
            var.set(chosen)

    # ---------- что нажато прямо сейчас ----------

    def _get_keyboard(self) -> Keyboard:
        if self._keyboard is None:
            self._keyboard = system_keyboard()
        return self._keyboard

    def _poll_live_keys(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.live_keys.set(f"Сейчас нажато: {format_snapshot(self._get_keyboard().snapshot())}")
        except Exception as exc:  # noqa: BLE001 — показываем причину прямо в окне, это и есть диагностика
            self.live_keys.set(f"Клавиши не читаются: {exc}")
            self.after(LIVE_POLL_MS * 10, self._poll_live_keys)  # сбой мог быть разовым: пробуем снова, но реже
            return
        self.after(LIVE_POLL_MS, self._poll_live_keys)

    def _open_input_monitoring(self) -> None:
        request_input_monitoring()
        if IS_MAC:
            subprocess.run(["open", INPUT_MONITORING_URL], check=False)

    # ---------- запись комбинации ----------

    def _start_capture(self) -> None:
        try:
            keyboard = self._get_keyboard()
            keyboard.snapshot()  # заранее проверяем, что чтение клавиш вообще работает
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Говорилка", f"Не получилось читать клавиши: {exc}", parent=self)
            return
        self._on_capture(True)
        self._recorder = ChordRecorder()
        self._deadline = time.monotonic() + CAPTURE_TIMEOUT_S
        self.hotkey_btn.configure(state="disabled", text="Нажмите…")
        self.hotkey_hint.set("Нажмите нужные клавиши вместе и отпустите. Нужен хотя бы один модификатор.")
        self._poll_capture(keyboard)

    def _poll_capture(self, keyboard) -> None:  # noqa: ANN001
        if not self.winfo_exists() or self._recorder is None:
            return
        try:
            chord = self._recorder.feed(keyboard.snapshot())
        except Exception as exc:  # noqa: BLE001
            self._finish_capture(f"Не получилось прочитать клавиши: {exc}")
            return
        if chord is not None:
            self._chord = chord
            self.hotkey_text.set(format_chord(chord))
            note = ""
            if conflicts_with_language_switch(chord, windows_language_switch()):
                note = " Внимание: у вас это же сочетание переключает язык ввода, лучше выбрать другое."
            self._finish_capture(f"Задано: {format_chord(chord)}.{note}")
        elif time.monotonic() > self._deadline:
            self._finish_capture("Комбинацию не нажали, оставил прежнюю.")
        else:
            self.after(CAPTURE_POLL_MS, lambda: self._poll_capture(keyboard))

    def _finish_capture(self, message: str) -> None:
        if self._recorder is not None:
            self._on_capture(False)
        self._recorder = None
        self.hotkey_btn.configure(state="normal", text="Изменить…")
        self.hotkey_hint.set(message)

    def destroy(self) -> None:
        if self._recorder is not None:  # закрыли окно посреди записи комбинации — вернуть фоновую клавишу
            self._recorder = None
            self._on_capture(False)
        super().destroy()

    # ---------- сохранение ----------

    def _save(self) -> None:
        prompt = self.prompt.get("1.0", "end").strip()
        settings = replace(
            self._settings,
            model=self.model_var.get(),
            transcripts_dir=self.dir_var.get().strip() or self._settings.transcripts_dir,
            claude_workdir=self.claude_var.get().strip(),
            claude_prompt=prompt or self._settings.claude_prompt,
            hotkey_enabled=self.hotkey_enabled_var.get(),
            hotkey=chord_to_string(self._chord),
            show_indicator=self.indicator_var.get(),
        )
        self._on_save(settings, self.autostart_var.get())
        self.destroy()
