"""Окно настроек: модель, папки, запрос для Claude Code."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from tkinter import filedialog, ttk

from govorilka.config import MODELS, Settings
from govorilka.transcript_view import FONT, FONT_SIZE


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, settings: Settings, on_save: Callable[[Settings], None]) -> None:
        super().__init__(parent)
        self.title("Настройки")
        self.geometry("660x460")
        self.transient(parent)
        self._settings = settings
        self._on_save = on_save
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Модель распознавания:").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self.model_var = tk.StringVar(value=settings.model)
        ttk.Combobox(frame, textvariable=self.model_var, values=list(MODELS), state="readonly").grid(
            row=1, column=0, sticky="w"
        )

        self.dir_var = tk.StringVar(value=settings.transcripts_dir)
        self.claude_var = tk.StringVar(value=settings.claude_workdir)
        self._path_row(frame, "Папка для транскриптов:", self.dir_var, 2)
        self._path_row(frame, "Где запускать Claude Code (пусто — в папке транскриптов):", self.claude_var, 4)

        ttk.Label(frame, text="Запрос для Claude Code ({file} — путь к транскрипту):").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 2)
        )
        self.prompt = tk.Text(frame, height=8, wrap="word", font=(FONT, FONT_SIZE - 1))
        self.prompt.insert("1.0", settings.claude_prompt)
        self.prompt.grid(row=7, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(7, weight=1)
        frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right", padx=6)

    def _path_row(self, frame: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Entry(frame, textvariable=var).grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(frame, text="…", width=3, command=lambda: self._browse(var)).grid(row=row + 1, column=1, padx=4)

    def _browse(self, var: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=var.get() or None)
        if chosen:
            var.set(chosen)

    def _save(self) -> None:
        prompt = self.prompt.get("1.0", "end").strip()
        self._on_save(
            replace(
                self._settings,
                model=self.model_var.get(),
                transcripts_dir=self.dir_var.get().strip() or self._settings.transcripts_dir,
                claude_workdir=self.claude_var.get().strip(),
                claude_prompt=prompt or self._settings.claude_prompt,
            )
        )
        self.destroy()
