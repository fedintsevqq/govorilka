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

from govorilka import autostart, single_instance
from govorilka.audio import AudioError
from govorilka.claude_bridge import ClaudeNotFoundError, build_prompt, open_in_claude
from govorilka.config import DATA_DIR, FROZEN, SOURCE_MIC, SOURCE_SYSTEM, Settings, load_settings, save_settings
from govorilka.engines import Engines, load_engines
from govorilka.filejob import CANCELLED, FILE_DIALOG_TYPES, FileJob
from govorilka.hotkeys import HotkeyWatcher, format_chord, parse_chord
from govorilka.indicator import NEUTRAL, OK, RECORDING, RecordingIndicator
from govorilka.pipeline import TextEvent
from govorilka.recognizer import RecognizerError
from govorilka.session import Session
from govorilka.settings_dialog import SettingsDialog
from govorilka.transcript import Transcript, format_offset, write_atomic
from govorilka.transcript_view import IS_MAC, ME_COLOR, OTHER_COLORS, TranscriptView

log = logging.getLogger(__name__)

POLL_MS = 80
BACKGROUND_FLAG = "--background"
LEVEL_DECAY = 0.85
METERS = {SOURCE_MIC: ("Я", ME_COLOR), SOURCE_SYSTEM: ("Собеседники", OTHER_COLORS[0])}


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = load_settings()
        self.engines: Engines | None = None
        self.session: Session | None = None
        self.file_job: FileJob | None = None
        self.last_file: Path | None = None
        self.transcript: Transcript | None = None
        self.stopping: threading.Thread | None = None
        self.partials: dict[str, TextEvent] = {}  # черновик фразы по каждому источнику
        self.levels = {source: 0.0 for source in METERS}
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.started_at = 0.0
        self._starting = False  # запись запускается: модели грузятся или открываются устройства
        self.indicator = RecordingIndicator(root)
        self.hotkeys = HotkeyWatcher(
            on_trigger=lambda: self.events.put(("hotkey", None)),
            on_error=lambda message: self.events.put(("status", message)),
        )
        self._apply_hotkey()
        self.hotkeys.start()

        root.title("Говорилка — транскрибация созвонов")
        root.geometry("980x660")
        root.minsize(720, 440)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.root.after(POLL_MS, self._poll)

    # ---------- интерфейс ----------

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Тема:").pack(side="left")
        self.title_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.title_var, width=24).pack(side="left", padx=(4, 12))

        self.mic_var = tk.BooleanVar(value=self.settings.capture_mic)
        self.sys_var = tk.BooleanVar(value=self.settings.capture_system)
        self.echo_var = tk.BooleanVar(value=self.settings.echo_filter)
        self.speakers_var = tk.BooleanVar(value=self.settings.split_speakers)
        for text, var in (
            ("Микрофон", self.mic_var),
            ("Звук компьютера", self.sys_var),
            ("Убирать эхо колонок", self.echo_var),
            ("Различать голоса", self.speakers_var),
        ):
            ttk.Checkbutton(top, text=text, variable=var).pack(side="left", padx=(0, 8))

        self.start_btn = ttk.Button(top, text="● Начать запись", command=self.toggle_recording)
        self.start_btn.pack(side="right")
        self.file_btn = ttk.Button(top, text="Открыть файл…", command=self.toggle_file)
        self.file_btn.pack(side="right", padx=6)
        self.timer_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.timer_var, width=8, anchor="e").pack(side="right", padx=4)

        meters = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        meters.pack(fill="x")
        self.meter_canvases: dict[str, tk.Canvas] = {}
        for source, (label, _) in METERS.items():
            ttk.Label(meters, text=f"{label}:").pack(side="left")
            canvas = tk.Canvas(meters, width=140, height=8, bg="#e6e6e6", highlightthickness=0)
            canvas.pack(side="left", padx=(4, 16))
            self.meter_canvases[source] = canvas
        self.progress = ttk.Progressbar(meters, length=220, maximum=1.0, mode="determinate")

        self.view = TranscriptView(self.root)

        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Отправить в Claude Code", command=self.send_to_claude).pack(side="right")
        ttk.Button(bottom, text="Настройки", command=self.open_settings).pack(side="right", padx=6)
        ttk.Button(bottom, text="Сохранить как…", command=self.save_as).pack(side="left")
        ttk.Button(bottom, text="Копировать", command=self.copy_text).pack(side="left", padx=6)
        ttk.Button(bottom, text="Папка с записями", command=self.open_folder).pack(side="left")

        self.status_var = tk.StringVar(value="Готово. Нажмите «Начать запись» или откройте аудио/видеофайл.")
        ttk.Label(self.root, textvariable=self.status_var, padding=(10, 0, 10, 8)).pack(fill="x")

    def _read_toggles(self) -> None:
        self.settings = replace(
            self.settings,
            capture_mic=self.mic_var.get(),
            capture_system=self.sys_var.get(),
            echo_filter=self.echo_var.get(),
            split_speakers=self.speakers_var.get(),
        )
        save_settings(self.settings)

    def _ensure_engines(self) -> Engines:
        """Вызывается из фонового потока: при первом запуске модели скачиваются."""
        engines = load_engines(self.settings.model, lambda s: self.events.put(("status", s)), self.engines)
        self.engines = engines
        return engines

    def _show_transcript(self, transcript: Transcript, path: Path) -> None:
        self.transcript = transcript
        self.last_file = path
        self.partials.clear()
        self.view.clear()

    # ---------- живая запись ----------

    def toggle_recording(self) -> None:
        if self.session:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.file_job:
            return
        self._read_toggles()
        self._starting = True
        self.start_btn.configure(state="disabled")
        self.file_btn.configure(state="disabled")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        try:
            session = Session(
                self.settings,
                self._ensure_engines(),
                self.title_var.get(),
                on_text=lambda e: self.events.put(("text", e)),
                on_level=lambda source, level: self.events.put(("level", (source, level))),
                on_notice=lambda message: self.events.put(("status", message)),
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

    def stop_recording(self) -> None:
        session = self.session
        if session is None:
            return
        self.session = None
        self.start_btn.configure(state="disabled")
        self.file_btn.configure(state="disabled")  # пока запись дописывается, файл открывать нельзя
        self.status_var.set("Останавливаю, дописываю последние фразы…")
        self.stopping = threading.Thread(target=lambda: self.events.put(("stopped", session.stop())), daemon=True)
        self.stopping.start()

    # ---------- расшифровка файла ----------

    def toggle_file(self) -> None:
        if self.file_job:
            self.file_job.cancel()
            self.status_var.set("Отменяю расшифровку…")
            return
        if self.session:
            return
        chosen = filedialog.askopenfilename(title="Аудио или видео для расшифровки", filetypes=FILE_DIALOG_TYPES)
        if not chosen:
            return
        self._read_toggles()
        self.start_btn.configure(state="disabled")
        self.file_btn.configure(state="disabled")
        threading.Thread(target=self._file_worker, args=(Path(chosen),), daemon=True).start()

    def _file_worker(self, path: Path) -> None:
        try:
            job = FileJob(
                path,
                self.settings,
                self._ensure_engines(),
                on_text=lambda e: self.events.put(("text", e)),
                on_progress=lambda value: self.events.put(("file_progress", value)),
                on_done=lambda saved, error: self.events.put(("file_done", (saved, error))),
            )
        except RecognizerError as exc:
            self.events.put(("error", str(exc)))
            return
        except Exception as exc:
            log.exception("Не удалось начать расшифровку")
            self.events.put(("error", f"Не удалось начать расшифровку: {exc}"))
            return
        self.events.put(("file_started", job))
        job.start()

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
                elapsed = format_offset(time.monotonic() - self.started_at)
                self.timer_var.set(elapsed)
                if self.settings.show_indicator:
                    self.indicator.update_text(f"Запись {elapsed}")
        except Exception:  # окно должно жить дальше, даже если одно событие сломалось
            log.exception("Ошибка обновления окна")
        finally:
            self.root.after(POLL_MS, self._poll)

    def _handle_event(self, kind: str, payload: object) -> bool:  # noqa: C901, PLR0912 — простой диспетчер
        if kind == "text":
            event: TextEvent = payload  # type: ignore[assignment]
            if event.kind == "partial":
                self.partials[event.source] = event
            else:
                self.partials.pop(event.source, None)
            return True
        if kind == "level":
            source, level = payload  # type: ignore[misc]
            if source in self.levels:
                self.levels[source] = max(self.levels[source], level)
            return False
        if kind == "status":
            self.status_var.set(str(payload))
            if self._starting and self.settings.show_indicator:
                self.indicator.show(str(payload), NEUTRAL)  # тихий запуск: окна нет, прогресс виден на плашке
        elif kind == "hotkey":
            self.hotkey_toggle()
        elif kind == "show":
            self.show_window()
        elif kind == "error":
            self._starting = False
            self.indicator.hide()
            self._set_idle()
            self.status_var.set(str(payload))
            self._alert("error", str(payload))
        elif kind == "started":
            self._starting = False
            self.session = payload  # type: ignore[assignment]
            self._show_transcript(self.session.transcript, self.session.path)
            self.started_at = time.monotonic()
            self.start_btn.configure(text="■ Остановить", state="normal")
            self.status_var.set(f"Идёт запись. Автосохранение: {self.session.path}")
            if self.settings.show_indicator:
                self.indicator.show("Запись 00:00", RECORDING)
            return True
        elif kind == "stopped":
            self._set_idle()
            self.status_var.set(f"Запись остановлена. Сохранено: {payload}")
            if self.settings.show_indicator:
                name = Path(str(payload)).name if payload else "не удалось сохранить"
                self.indicator.show(f"Сохранено: {name}", OK, ttl_ms=4000)
            return True
        elif kind == "file_started":
            self.file_job = payload  # type: ignore[assignment]
            self._show_transcript(self.file_job.transcript, self.file_job.path)
            self.file_btn.configure(text="Отменить расшифровку", state="normal")
            self.progress.configure(value=0)
            self.progress.pack(side="right")
            self.status_var.set(f"Расшифровываю {self.file_job.source_path.name}…")
            return True
        elif kind == "file_progress":
            value = float(payload)  # type: ignore[arg-type]
            self.progress.configure(value=value)
            if self.file_job:
                self.status_var.set(f"Расшифровываю {self.file_job.source_path.name}: {int(value * 100)}%")
        elif kind == "file_done":
            saved, error = payload  # type: ignore[misc]
            self.file_job = None
            self.progress.pack_forget()
            self._set_idle()
            if saved is None and error != CANCELLED:
                self._alert("error", error or "Не получилось расшифровать файл")
                self.status_var.set(error or "Не получилось расшифровать файл")
            elif saved is None:
                self.status_var.set("Расшифровка отменена")
            else:
                note = "Готово" if error is None else f"{error} (сохранено то, что успели расшифровать)"
                self.status_var.set(f"{note}. Сохранено: {saved}")
            return True
        return False

    def _alert(self, level: str, message: str) -> None:
        """Диалог модальный и крутит свой цикл, поэтому показываем его не из _poll, иначе метры и текст замерзают."""
        show = messagebox.showerror if level == "error" else messagebox.showwarning
        self.root.after_idle(lambda: show("Говорилка", message))

    def _set_idle(self) -> None:
        self.partials.clear()
        self.timer_var.set("")
        busy = self.file_job is not None  # расшифровка файла может идти, пока дописывается запись
        self.start_btn.configure(text="● Начать запись", state="disabled" if busy else "normal")
        if busy:
            self.file_btn.configure(text="Отменить расшифровку", state="normal")
        else:
            self.file_btn.configure(text="Открыть файл…", state="normal")

    def _render(self) -> None:
        utterances = self.transcript.utterances if self.transcript else []
        self.view.show(utterances, list(self.partials.values()))

    def _update_meters(self) -> None:
        for source, canvas in self.meter_canvases.items():
            level = min(1.0, self.levels[source] * 3)
            canvas.delete("all")
            if level > 0.01:
                canvas.create_rectangle(0, 0, int(140 * level), 8, fill=METERS[source][1], width=0)
            self.levels[source] *= LEVEL_DECAY

    # ---------- кнопки ----------

    def save_as(self) -> None:
        transcript = self.transcript
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
        if not self.transcript:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.transcript.to_plain_text())
        self.status_var.set("Транскрипт скопирован в буфер обмена")

    def open_folder(self) -> None:
        folder = self.settings.transcripts_path
        folder.mkdir(parents=True, exist_ok=True)
        if IS_MAC:
            subprocess.run(["open", str(folder)], check=False)
        else:
            os.startfile(folder)  # noqa: S606 — открываем папку в проводнике

    def send_to_claude(self) -> None:
        saved = True
        if self.session:
            saved = self.session.save() is not None
        if self.file_job:
            saved = self.file_job.saver.save() is not None and saved
        if not saved:
            messagebox.showerror(
                "Говорилка",
                "Не получилось сохранить свежий транскрипт, Claude увидел бы старую версию. "
                "Освободите место на диске или смените папку в настройках.",
            )
            return
        if not self.last_file or not self.last_file.exists():
            messagebox.showinfo("Говорилка", "Сначала запишите созвон или расшифруйте файл — отправлять пока нечего.")
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

    # ---------- горячая клавиша, фон ----------

    def _apply_hotkey(self) -> None:
        """Передаёт наблюдателю актуальную комбинацию (или выключает её)."""
        if not self.settings.hotkey_enabled:
            self.hotkeys.set_chord(None)
            return
        try:
            self.hotkeys.set_chord(parse_chord(self.settings.hotkey))
        except ValueError as exc:
            self.hotkeys.set_chord(None)
            self.status_var.set(f"Горячая клавиша не включена: {exc}")

    def hotkey_toggle(self) -> None:
        """Старт или стоп записи по горячей клавише. Пока идёт запуск, остановка или расшифровка файла, игнорируем."""
        stopping = self.stopping is not None and self.stopping.is_alive()
        if self.file_job or self._starting or stopping:
            return
        self.toggle_recording()

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, autostart.is_enabled(), self._apply_settings)

    def _apply_settings(self, settings: Settings, autostart_on: bool) -> None:
        self.settings = settings
        save_settings(settings)
        self._apply_hotkey()
        if not settings.show_indicator:
            self.indicator.hide()
        message = "Настройки сохранены"
        if settings.hotkey_enabled:
            message += f". Горячая клавиша: {format_chord(parse_chord(settings.hotkey))}"
        try:
            if autostart_on != autostart.is_enabled():
                autostart.set_enabled(autostart_on)
        except OSError as exc:
            log.exception("Не удалось изменить автозапуск")
            self._alert("error", f"Не получилось изменить автозапуск: {exc}")
        self.status_var.set(message)

    def on_close(self) -> None:
        self.hotkeys.stop()
        self.status_var.set("Сохраняю запись…")
        self.root.update_idletasks()
        try:
            if self.session:
                self.session.stop()
            if self.stopping:
                self.stopping.join()  # иначе процесс завершится, не дописав хвост созвона
            if self.file_job:
                self.file_job.cancel()
                self.file_job.wait()  # сохранит то, что успел расшифровать
        except Exception:
            log.exception("Ошибка при сохранении перед выходом")
        finally:
            self.root.destroy()


def main() -> None:
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    # у оконного .exe и у pythonw нет stdout/stderr, а прогресс-бар скачивания модели пишет в stderr
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if FROZEN:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        # у собранного приложения нет консоли — пишем лог в файл, чтобы было что прислать при сбое
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=log_format, filename=DATA_DIR / "govorilka.log", encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        from govorilka.selftest import run

        sys.exit(run(Path(sys.argv[2])))
    background = BACKGROUND_FLAG in sys.argv
    # Сначала занимаем «единственное место»: вторая копия не должна даже открывать окно и читать клавиши.
    # Просьба «покажись» от второго запуска попадает в очередь окна, когда оно уже создано.
    shown: list[App] = []
    lock = single_instance.acquire(
        lambda: shown[0].events.put(("show", None)) if shown else None, notify_existing=not background
    )
    if lock is None:
        return
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("aqua" if IS_MAC else "vista")
    except tk.TclError:
        pass
    app = App(root)
    shown.append(app)
    if IS_MAC:
        root.createcommand("::tk::mac::ReopenApplication", app.show_window)  # клик по значку в Dock
    if background:
        root.iconify()  # автозапуск: окно не выскакивает, работает горячая клавиша
    try:
        root.mainloop()
    finally:
        lock.close()


if __name__ == "__main__":
    main()
