"""Окно приложения «Говорилка»."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from govorilka.audio import AudioError
from govorilka.claude_bridge import ClaudeNotFoundError, build_prompt, open_in_claude
from govorilka.config import (
    DATA_DIR,
    FROZEN,
    MODELS,
    SPEAKER_ME,
    SPEAKER_OTHERS,
    Settings,
    load_settings,
    save_settings,
)
from govorilka.pipeline import TextEvent
from govorilka.recognizer import Recognizer, RecognizerError
from govorilka.session import Session
from govorilka.transcript import Transcript, format_offset, write_atomic

log = logging.getLogger(__name__)

POLL_MS = 80
LEVEL_DECAY = 0.85
IS_MAC = sys.platform == "darwin"
FONT = "Helvetica Neue" if IS_MAC else "Segoe UI"
MONO = "Menlo" if IS_MAC else "Consolas"
FONT_SIZE = 13 if IS_MAC else 11  # на Mac точки мельче
COLORS = {SPEAKER_ME: "#1f6feb", SPEAKER_OTHERS: "#1a7f37"}


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = load_settings()
        self.recognizer: Recognizer | None = None
        self.session: Session | None = None
        self.last_file: Path | None = None
        self.transcript: Transcript | None = None
        self.stopping: threading.Thread | None = None
        self.partials: dict[str, TextEvent] = {}
        self.levels = {SPEAKER_ME: 0.0, SPEAKER_OTHERS: 0.0}
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.started_at = 0.0

        root.title("Говорилка — транскрибация созвонов")
        root.geometry("900x640")
        root.minsize(640, 420)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.root.after(POLL_MS, self._poll)

    # ---------- интерфейс ----------

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Тема:").pack(side="left")
        self.title_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.title_var, width=28).pack(side="left", padx=(4, 12))

        self.mic_var = tk.BooleanVar(value=self.settings.capture_mic)
        self.sys_var = tk.BooleanVar(value=self.settings.capture_system)
        ttk.Checkbutton(top, text="Микрофон", variable=self.mic_var).pack(side="left")
        ttk.Checkbutton(top, text="Звук компьютера", variable=self.sys_var).pack(side="left", padx=(8, 8))
        self.echo_var = tk.BooleanVar(value=self.settings.echo_filter)
        ttk.Checkbutton(top, text="Убирать эхо колонок", variable=self.echo_var).pack(side="left", padx=(0, 12))

        self.model_var = tk.StringVar(value=self.settings.model)
        ttk.Combobox(top, textvariable=self.model_var, values=list(MODELS), state="readonly", width=20).pack(
            side="left"
        )

        self.start_btn = ttk.Button(top, text="● Начать запись", command=self.toggle)
        self.start_btn.pack(side="right")
        self.timer_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.timer_var, width=8, anchor="e").pack(side="right", padx=8)

        meters = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        meters.pack(fill="x")
        self.meter_canvases: dict[str, tk.Canvas] = {}
        for speaker in (SPEAKER_ME, SPEAKER_OTHERS):
            ttk.Label(meters, text=f"{speaker}:").pack(side="left")
            canvas = tk.Canvas(meters, width=140, height=8, bg="#e6e6e6", highlightthickness=0)
            canvas.pack(side="left", padx=(4, 16))
            self.meter_canvases[speaker] = canvas

        body = ttk.Frame(self.root, padding=(10, 0, 10, 0))
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", font=(FONT, FONT_SIZE), padx=10, pady=8, relief="flat", bg="#fbfbfb")
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("time", foreground="#8b8b8b", font=(MONO, FONT_SIZE - 2))
        for speaker, color in COLORS.items():
            self.text.tag_configure(speaker, foreground=color, font=(FONT, FONT_SIZE, "bold"))
        self.text.tag_configure("partial", foreground="#9a9a9a", font=(FONT, FONT_SIZE, "italic"))

        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Отправить в Claude Code", command=self.send_to_claude).pack(side="right")
        ttk.Button(bottom, text="Настройки", command=self.open_settings).pack(side="right", padx=6)
        ttk.Button(bottom, text="Сохранить как…", command=self.save_as).pack(side="left")
        ttk.Button(bottom, text="Копировать", command=self.copy_text).pack(side="left", padx=6)
        ttk.Button(bottom, text="Папка с записями", command=self.open_folder).pack(side="left")

        self.status_var = tk.StringVar(value="Готово. Нажмите «Начать запись».")
        ttk.Label(self.root, textvariable=self.status_var, padding=(10, 0, 10, 8), foreground="#555").pack(
            fill="x"
        )

    # ---------- запись ----------

    def toggle(self) -> None:
        if self.session:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        self.settings = replace(
            self.settings,
            model=self.model_var.get(),
            capture_mic=self.mic_var.get(),
            capture_system=self.sys_var.get(),
            echo_filter=self.echo_var.get(),
        )
        save_settings(self.settings)
        self.start_btn.configure(state="disabled")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        try:
            if self.recognizer is None or self.recognizer.model_name != self.settings.model:
                self.recognizer = Recognizer(self.settings.model, on_status=lambda s: self.events.put(("status", s)))
            session = Session(
                self.settings,
                self.recognizer,
                self.title_var.get(),
                on_text=lambda e: self.events.put(("text", e)),
                on_level=lambda speaker, level: self.events.put(("level", (speaker, level))),
            )
            session.start()
        except (AudioError, RecognizerError) as exc:
            self.events.put(("error", str(exc)))
            return
        except Exception as exc:
            log.exception("Не удалось начать запись")
            self.events.put(("error", f"Не удалось начать запись: {exc}"))
            return
        self.events.put(("started", session))

    def stop(self) -> None:
        session = self.session
        if session is None:
            return
        self.session = None
        self.start_btn.configure(state="disabled")
        self.status_var.set("Останавливаю, дописываю последние фразы…")
        self.stopping = threading.Thread(target=lambda: self.events.put(("stopped", session.stop())), daemon=True)
        self.stopping.start()

    # ---------- события из фоновых потоков ----------

    def _poll(self) -> None:
        try:
            dirty = False
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
                dirty |= self._handle_event(kind, payload)
            if dirty:
                self._render()
            self._update_meters()
            if self.session:
                self.timer_var.set(format_offset(time.monotonic() - self.started_at))
        except Exception:  # окно должно жить дальше, даже если одно событие сломалось
            log.exception("Ошибка обновления окна")
        finally:
            self.root.after(POLL_MS, self._poll)

    def _handle_event(self, kind: str, payload: object) -> bool:
        if kind == "text":
            event: TextEvent = payload  # type: ignore[assignment]
            if event.kind == "partial":
                self.partials[event.speaker] = event
            else:
                self.partials.pop(event.speaker, None)
            return True
        if kind == "level":
            speaker, level = payload  # type: ignore[misc]
            self.levels[speaker] = max(self.levels[speaker], level)
            return False
        if kind == "status":
            self.status_var.set(str(payload))
        elif kind == "error":
            self.start_btn.configure(state="normal")
            self.status_var.set(str(payload))
            messagebox.showerror("Говорилка", str(payload))
        elif kind == "started":
            self.session = payload  # type: ignore[assignment]
            self.last_file = self.session.path
            self.transcript = self.session.transcript
            self.partials.clear()
            self.started_at = time.monotonic()
            self.start_btn.configure(text="■ Остановить", state="normal")
            self.status_var.set(f"Идёт запись. Автосохранение: {self.session.path}")
            return True
        elif kind == "stopped":
            self.partials.clear()
            self.start_btn.configure(text="● Начать запись", state="normal")
            self.timer_var.set("")
            self.status_var.set(f"Запись остановлена. Сохранено: {payload}")
            return True
        return False

    def _current_transcript(self) -> Transcript | None:
        return self.transcript

    def _render(self) -> None:
        transcript = self.transcript
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if transcript:
            for u in transcript.utterances:
                self._insert_line(u.start, u.speaker, u.text, None)
        for event in sorted(self.partials.values(), key=lambda e: e.start):
            if event.text:
                self._insert_line(event.start, event.speaker, event.text + " …", "partial")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _insert_line(self, start: float, speaker: str, text: str, extra_tag: str | None) -> None:
        self.text.insert("end", f"[{format_offset(start)}] ", "time")
        self.text.insert("end", f"{speaker}: ", speaker)
        self.text.insert("end", text + "\n\n", extra_tag or ())

    def _update_meters(self) -> None:
        for speaker, canvas in self.meter_canvases.items():
            level = min(1.0, self.levels[speaker] * 3)
            canvas.delete("all")
            if level > 0.01:
                canvas.create_rectangle(0, 0, int(140 * level), 8, fill=COLORS[speaker], width=0)
            self.levels[speaker] *= LEVEL_DECAY

    # ---------- кнопки ----------

    def save_as(self) -> None:
        transcript = self._current_transcript()
        if not transcript or not transcript.utterances:
            messagebox.showinfo("Говорилка", "Пока нечего сохранять.")
            return
        path = filedialog.asksaveasfilename(
            initialfile=transcript.default_filename(),
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Текст", "*.txt")],
        )
        if not path:
            return
        target = Path(path)
        text = transcript.to_plain_text() if target.suffix == ".txt" else transcript.to_markdown()
        try:
            write_atomic(target, text)
        except OSError as exc:
            messagebox.showerror("Говорилка", f"Не сохранил файл: {exc}")
            return
        self.status_var.set(f"Сохранено: {target}")

    def copy_text(self) -> None:
        transcript = self._current_transcript()
        if not transcript:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(transcript.to_plain_text())
        self.status_var.set("Транскрипт скопирован в буфер обмена")

    def open_folder(self) -> None:
        folder = self.settings.transcripts_path
        folder.mkdir(parents=True, exist_ok=True)
        if IS_MAC:
            subprocess.run(["open", str(folder)], check=False)
        else:
            os.startfile(folder)  # noqa: S606 — открываем папку в проводнике

    def send_to_claude(self) -> None:
        if self.session:
            self.session.save()
        if not self.last_file or not self.last_file.exists():
            messagebox.showinfo("Говорилка", "Сначала запишите созвон — отправлять пока нечего.")
            return
        prompt = build_prompt(self.last_file, self.settings.claude_prompt)
        try:
            open_in_claude(self.last_file, self.settings.claude_path, prompt)
        except ClaudeNotFoundError as exc:
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
            messagebox.showwarning("Говорилка", str(exc))
            return
        except OSError as exc:
            messagebox.showerror("Говорилка", f"Не запустил Claude Code: {exc}")
            return
        self.status_var.set(f"Открыл Claude Code с файлом {self.last_file.name}")

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, self._apply_settings)

    def _apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        save_settings(settings)
        self.status_var.set("Настройки сохранены")

    def on_close(self) -> None:
        self.status_var.set("Сохраняю запись…")
        self.root.update_idletasks()
        try:
            if self.session:
                self.session.stop()
            if self.stopping:
                self.stopping.join()  # иначе процесс завершится, не дописав хвост созвона
        except Exception:
            log.exception("Ошибка при сохранении перед выходом")
        finally:
            self.root.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, settings: Settings, on_save) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.title("Настройки")
        self.geometry("640x400")
        self.transient(parent)
        self._settings = settings
        self._on_save = on_save
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        self.dir_var = tk.StringVar(value=settings.transcripts_dir)
        self.claude_var = tk.StringVar(value=settings.claude_workdir)
        self._path_row(frame, "Папка для транскриптов:", self.dir_var, 0)
        self._path_row(frame, "Где запускать Claude Code (пусто — в папке транскриптов):", self.claude_var, 2)

        ttk.Label(frame, text="Запрос для Claude Code ({file} — путь к транскрипту):").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 2)
        )
        self.prompt = tk.Text(frame, height=8, wrap="word", font=(FONT, FONT_SIZE - 1))
        self.prompt.insert("1.0", settings.claude_prompt)
        self.prompt.grid(row=5, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(5, weight=1)
        frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right", padx=6)

    def _path_row(self, frame: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 2))
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
                transcripts_dir=self.dir_var.get().strip() or self._settings.transcripts_dir,
                claude_workdir=self.claude_var.get().strip(),
                claude_prompt=prompt or self._settings.claude_prompt,
            )
        )
        self.destroy()


def main() -> None:
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if FROZEN:
        # у оконного .exe нет stdout/stderr, а прогресс-бар скачивания модели пишет в stderr
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        # у собранного приложения нет консоли — пишем лог в файл, чтобы было что прислать при сбое
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=log_format, filename=DATA_DIR / "govorilka.log", encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        from govorilka.selftest import run

        sys.exit(run(Path(sys.argv[2])))
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("aqua" if IS_MAC else "vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
