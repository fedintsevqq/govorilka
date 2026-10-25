"""Логика приложения без интерфейса.

Окно, трей и круговое меню только зовут методы контроллера и слушают его события. Так одно и то же
поведение работает из любого места интерфейса и проверяется тестами без экрана.

Методы вызываются из потока интерфейса. События приходят из любых потоков: интерфейс сам
переносит их в свой поток (в Qt это делают сигналы).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
import subprocess
import sys
import threading
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from govorilka import autostart, permissions, single_instance
from govorilka.audio import AudioError
from govorilka.audio_base import PermissionDeniedError
from urllib.parse import quote

from govorilka import ai, desktop_apps, models, summary, updates, version
from govorilka.claude_bridge import ClaudeNotFoundError, build_prompt, open_in_claude
from govorilka.dictation import Dictation
from govorilka.config import DATA_DIR, SPEAKER_FILE, SPEAKER_OTHERS, Settings, load_settings, save_settings
from govorilka.engines import Engines, load_engines
from govorilka.filejob import CANCELLED, FileJob
from govorilka.hotkeys import (
    HotkeyWatcher,
    chord_to_string,
    conflicts_with_language_switch,
    format_chord,
    input_monitoring_status,
    parse_chord,
    request_input_monitoring,
    windows_language_switch,
)
from govorilka.pipeline import TextEvent
from govorilka.refine import refine_transcript, remember_names
from govorilka.recognizer import RecognizerError
from govorilka.session import Session
from govorilka.transcript import Transcript, write_atomic
from govorilka.voices import VoiceBook

log = logging.getLogger(__name__)

AUTO_CHECK_DELAY_S = 8.0  # даём окну и моделям спокойно запуститься
AUTO_CHECK_EVERY_S = 24 * 3600


class AppState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"  # грузятся модели, открываются устройства
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"  # дописываются последние фразы
    FILE = "file"  # расшифровывается файл


class Listener:
    """События контроллера. По умолчанию ничего не делают — переопределяйте нужные."""

    def on_state(self, state: AppState) -> None: ...
    def on_status(self, text: str) -> None: ...
    def on_text(self, event: TextEvent) -> None: ...
    def on_level(self, source: str, level: float) -> None: ...
    def on_transcript(self, transcript: Transcript, path: Path) -> None: ...
    def on_file_progress(self, value: float) -> None: ...
    def on_error(self, text: str) -> None: ...
    def on_permission(self, error: PermissionDeniedError) -> None: ...
    def on_hotkey(self) -> None: ...
    def on_hotkey_release(self) -> None: ...
    def on_finished(self, path: Path | None, message: str) -> None: ...
    def on_update(self, info: object | None, error: str | None, manual: bool) -> None: ...  # UpdateInfo, текст ошибки
    def on_models_unloaded(self) -> None: ...  # сборку мусора надо делать в главном потоке: из фонового она трогает объекты Qt
    def on_dictation(self, phase: str) -> None: ...  # "loading" / "listening" / "recognizing" / "" — диктовка закончилась
    def on_dictation_text(self, text: str) -> None: ...  # распознанный текст: окно вставит его у курсора


@dataclass
class _DictationRun:
    """Одна диктовка от нажатия до вставки текста."""

    released: threading.Event = field(default_factory=threading.Event)
    dictation: Dictation | None = None
    cancelled: bool = False
    finishing: bool = False


@dataclass(frozen=True)
class ClaudeResult:
    ok: bool
    message: str
    clipboard: str | None = None  # что положить в буфер (текст для чата или запрос, если Claude Code не нашёлся)
    url: str = ""  # какой чат открыть в браузере
    target: str = ""  # какой это чат: окну нужно, чтобы вставить разговор, когда чат откроется


class Controller:
    def __init__(
        self,
        listener: Listener,
        settings: Settings | None = None,
        hotkeys: HotkeyWatcher | None = None,
    ) -> None:
        self.listener = listener
        self.settings = settings or load_settings()
        self.state = AppState.IDLE
        self.engines: Engines | None = None
        self.session: Session | None = None
        self.file_job: FileJob | None = None
        self.transcript: Transcript | None = None
        self.last_file: Path | None = None
        self._stopping: threading.Thread | None = None
        self._unload_timer: threading.Timer | None = None
        self._update_timer: threading.Timer | None = None
        self.update_info: updates.UpdateInfo | None = None
        self._voice_book: VoiceBook | None = None
        self._closing = False  # идёт выход: новые записи не начинаем
        self._probe_country = ai.probe_country  # подменяются в тестах: настоящие проверки ходят в сеть
        self._reachable = ai.reachable
        self._dictation_run: _DictationRun | None = None
        self._dictation_lock = threading.Lock()
        self.hotkeys = hotkeys or HotkeyWatcher(
            on_trigger=listener.on_hotkey,
            on_error=listener.on_status,
            on_release=listener.on_hotkey_release,
            on_dictate_start=self.start_dictation,
            on_dictate_stop=self.stop_dictation,
        )
        self.apply_hotkey()

    # ---------- жизненный цикл ----------

    def start(self) -> None:
        self.hotkeys.start()
        self._schedule_update_check(AUTO_CHECK_DELAY_S)

    # ---------- обновления ----------

    def _schedule_update_check(self, delay_s: float) -> None:
        """Автопроверка: через несколько секунд после запуска и дальше раз в сутки. Из исходников не проверяем."""
        if self._closing or not self.settings.check_updates or not version.is_release():
            return
        timer = threading.Timer(delay_s, self._auto_check)
        timer.daemon = True
        self._update_timer = timer
        timer.start()

    def _auto_check(self) -> None:
        self.check_for_update(manual=False)
        self._schedule_update_check(AUTO_CHECK_EVERY_S)

    def check_for_update(self, manual: bool = True) -> None:
        """Спрашивает у GitHub про новую версию в фоне. Результат приходит в listener.on_update. Автопроверка молчит о
        сбоях и о версиях, от которых человек отказался; ручная отвечает всегда."""

        def work() -> None:
            info, error = None, None
            try:
                info = updates.check()
            except updates.UpdateError as exc:
                error = str(exc)
                log.info("Проверка обновлений не удалась: %s", exc)
            except Exception as exc:  # noqa: BLE001 — проверка обновлений не должна ничего ронять
                log.exception("Сбой проверки обновлений")
                error = str(exc)
            log.info("Проверка обновлений (%s): у вас %s, новее: %s", "вручную" if manual else "сама", version.VERSION,
                     info.version if info else error or "нет")
            if info is not None and not manual and info.version == self.settings.skipped_update:
                info = None
            if self._closing:
                return
            self.update_info = info
            self.listener.on_update(info, error, manual)

        threading.Thread(target=work, name="check-update", daemon=True).start()

    def prefetch_models(self) -> None:
        """Первый запуск: модели (около 880 МБ) качаются сразу в фоне, а не при первом нажатии записи."""
        def work() -> None:
            try:
                models.prefetch(self.settings.model, self.listener.on_status)
            except Exception as exc:  # noqa: BLE001 — не скачалось сейчас: докачаем при первой записи
                log.warning("Не скачал модели заранее: %s", exc)
                self.listener.on_status(f"Не скачал модели заранее ({exc}). Докачаю при первой записи")

        if models.missing(self.settings.model):
            threading.Thread(target=work, name="prefetch-models", daemon=True).start()

    def startup_notices(self) -> list[str]:
        """Что стоит сказать при запуске: нет разрешения, конфликт с раскладкой, старая версия рядом."""
        notices = []
        if self.settings.hotkey_enabled and input_monitoring_status() is False:
            request_input_monitoring()
            notices.append(
                "Горячая клавиша не будет работать, пока Говорилка не включена в «Системные настройки → "
                "Конфиденциальность и безопасность → Мониторинг ввода». После включения перезапустите приложение."
            )
        try:
            chord = parse_chord(self.settings.hotkey)
            if self.settings.hotkey_enabled and conflicts_with_language_switch(chord, windows_language_switch()):
                notices.append(
                    f"{format_chord(chord)} у вас ещё и переключает язык ввода: при записи сменится раскладка. "
                    "Задайте другую комбинацию в настройках, например с буквой."
                )
        except ValueError:
            notices.append(f"Комбинация «{self.settings.hotkey}» в настройках не распознана, горячая клавиша выключена.")
        try:
            autostart.refresh()  # обновили или перенесли программу — автозапуск должен указывать на новое место
        except OSError:
            log.exception("Не получилось поправить автозапуск")
            notices.append("Не получилось обновить автозапуск после обновления программы. Включите его заново в настройках.")
        if single_instance.legacy_instance_suspected():
            notices.append(
                "Похоже, ещё работает старая версия Говорилки. Закройте её, иначе горячая клавиша сработает в обеих."
            )
        return notices

    def shutdown(self) -> None:
        """Выход: дописать запись и расшифровку, остановить опрос клавиш."""
        self._closing = True
        if self._update_timer is not None:
            self._update_timer.cancel()
        self.hotkeys.stop()
        if self.state == AppState.RECORDING or self.state == AppState.PAUSED:
            log.info("Выход во время записи: голоса не уточняются, в файле подписи как были по ходу записи")
        try:
            if self.session:
                self.session.stop()
            if self._stopping:
                self._stopping.join()  # иначе процесс завершится, не дописав хвост созвона
            if self.file_job:
                self.file_job.cancel()
                self.file_job.wait()  # сохранит то, что успел расшифровать
        except Exception:
            log.exception("Ошибка при сохранении перед выходом")

    def _set_state(self, state: AppState) -> None:
        self.state = state
        self.listener.on_state(state)

    # ---------- модели ----------

    def _ensure_engines(self) -> Engines:
        """Вызывается из фонового потока: при первом запуске модели скачиваются."""
        self._cancel_unload()
        self.engines = load_engines(self.settings.model, self.listener.on_status, self.engines)
        return self.engines

    # ---------- память: модели выгружаются, когда запись не идёт ----------

    def _schedule_unload(self) -> None:
        """Модели занимают около гигабайта. Если запись не идёт, через unload_after_min минут они выгружаются:
        в трее остаётся ~200 МБ, а следующая запись начнётся на пару секунд позже."""
        self._cancel_unload()
        minutes = self.settings.unload_after_min
        if minutes <= 0 or self._closing or self.engines is None:
            return
        timer = threading.Timer(minutes * 60, self.unload_models)
        timer.daemon = True
        self._unload_timer = timer
        timer.start()

    def _cancel_unload(self) -> None:
        timer, self._unload_timer = self._unload_timer, None
        if timer is not None:
            timer.cancel()

    def unload_models(self) -> bool:
        """Выгружает модели, если ими никто не пользуется. True — выгрузили."""
        if self.engines is None or self.state != AppState.IDLE or self.busy or self.session is not None:
            return False
        if self._dictation_run is not None:
            return False
        self.engines = None
        self._unload_timer = None
        self.listener.on_models_unloaded()  # веса освобождаются сами, когда на них не остаётся ссылок (проверено замером)
        log.info("Модели выгружены из памяти: запись не идёт")
        return True

    # ---------- живая запись ----------

    @property
    def busy(self) -> bool:
        stopping = self._stopping is not None and self._stopping.is_alive()
        return self.state in (AppState.STARTING, AppState.STOPPING, AppState.FILE) or stopping

    def toggle_recording(self, title: str = "") -> None:
        if self.state in (AppState.RECORDING, AppState.PAUSED):
            self.stop_recording()
        elif not self.busy:
            self.start_recording(title)

    def start_recording(self, title: str = "") -> bool:
        if self.state != AppState.IDLE or self.busy:
            return False
        self._set_state(AppState.STARTING)
        threading.Thread(target=self._start_worker, args=(title,), name="start-recording", daemon=True).start()
        return True

    def _start_worker(self, title: str) -> None:
        try:
            engines = self._ensure_engines()
            if self._closing:  # пока грузились модели, программу закрыли: устройства уже не открываем
                self._set_state(AppState.IDLE)
                return
            session = Session(
                self.settings,
                engines,
                title,
                on_text=self.listener.on_text,
                on_level=self.listener.on_level,
                on_notice=self.listener.on_status,
            )
            session.start()
        except PermissionDeniedError as exc:
            self._set_state(AppState.IDLE)
            self.listener.on_permission(exc)
            return
        except (AudioError, RecognizerError) as exc:
            self._set_state(AppState.IDLE)
            self.listener.on_error(str(exc))
            return
        except Exception as exc:
            log.exception("Не удалось начать запись")
            self._set_state(AppState.IDLE)
            self.listener.on_error(f"Не удалось начать запись: {exc}")
            return
        self.session = session
        self.transcript = session.transcript
        self.last_file = session.path
        self.listener.on_transcript(session.transcript, session.path)
        self._set_state(AppState.RECORDING)
        self.listener.on_status(f"Идёт запись. Автосохранение: {session.path}")

    def stop_recording(self) -> None:
        session = self.session
        if session is None or self.state not in (AppState.RECORDING, AppState.PAUSED):
            return
        self.session = None
        self._set_state(AppState.STOPPING)
        self.listener.on_status("Останавливаю, дописываю последние фразы…")

        def finish() -> None:
            saved: Path | None = None
            failure: str | None = None
            try:
                saved = session.stop()
                self._after_recording(session)
            except Exception as exc:  # что бы ни случилось, приложение не должно остаться в «Сохраняю…» с мёртвыми кнопками
                log.exception("Ошибка при остановке записи")
                failure = f"Запись остановилась с ошибкой: {exc}. Проверьте папку с транскриптами."
            finally:
                self._set_state(AppState.IDLE)
            if failure:
                self.listener.on_error(failure)
            else:
                self.listener.on_finished(saved, f"Запись остановлена. Сохранено: {saved}")
            self._schedule_unload()  # после уведомлений: интерфейс узнаёт о конце записи без задержки

        self._stopping = threading.Thread(target=finish, name="stop-recording", daemon=True)
        self._stopping.start()

    def _after_recording(self, session: Session) -> None:
        self._refine(session.transcript, session.diarizers(), {"system": SPEAKER_OTHERS}, session.saver.save)

    def toggle_pause(self) -> None:
        if self.session is None:
            return
        if self.state == AppState.RECORDING:
            self.session.pause()
            self._set_state(AppState.PAUSED)
            self.listener.on_status("Пауза. Звук не записывается.")
        elif self.state == AppState.PAUSED:
            self.session.resume()
            self._set_state(AppState.RECORDING)
            self.listener.on_status("Запись продолжается")

    def add_mark(self) -> bool:
        if self.session is None:
            return False
        self.session.add_mark()
        self.listener.on_status("Момент отмечен ★")
        return True

    def elapsed(self) -> float:
        return self.session.elapsed() if self.session else 0.0

    # ---------- расшифровка файла ----------

    def open_file(self, path: Path) -> bool:
        if self.state != AppState.IDLE or self.busy:
            return False
        self._set_state(AppState.FILE)
        threading.Thread(target=self._file_worker, args=(path,), name="open-file", daemon=True).start()
        return True

    def _file_worker(self, path: Path) -> None:
        try:
            job = FileJob(
                path,
                self.settings,
                self._ensure_engines(),
                on_text=self.listener.on_text,
                on_progress=self.listener.on_file_progress,
                on_done=self._file_done,
            )
        except Exception as exc:
            log.exception("Не удалось начать расшифровку")
            self._set_state(AppState.IDLE)
            self.listener.on_error(str(exc) if isinstance(exc, RecognizerError) else f"Не удалось начать расшифровку: {exc}")
            return
        self.file_job = job
        self.transcript = job.transcript
        self.last_file = job.path
        self.listener.on_transcript(job.transcript, job.path)
        self.listener.on_status(f"Расшифровываю {path.name}…")
        job.start()

    def _file_done(self, saved: Path | None, error: str | None) -> None:
        job = self.file_job
        if job is not None and saved is not None:
            try:
                self._after_file(job)
            except Exception:
                log.exception("Ошибка после расшифровки файла")
        self.file_job = None
        self._set_state(AppState.IDLE)
        if saved is None and error != CANCELLED:
            self.listener.on_error(error or "Не получилось расшифровать файл")
        elif saved is None:
            self.listener.on_finished(None, "Расшифровка отменена")
        else:
            note = "Готово" if error is None else f"{error} (сохранено то, что успели расшифровать)"
            self.listener.on_finished(saved, f"{note}. Сохранено: {saved}")
        self._schedule_unload()

    def _after_file(self, job: FileJob) -> None:
        if job.diarizer is not None and job.diarizer.history:
            self._refine(job.transcript, {"file": job.diarizer}, {"file": SPEAKER_FILE}, job.saver.save)

    # ---------- голоса: уточнение, имена, память ----------

    def _book(self) -> VoiceBook:
        """Одна память голосов на приложение: файл не перечитывается при каждом обращении."""
        if self._voice_book is None:
            book = VoiceBook(DATA_DIR / "voices.json")
            if book.load_error:  # не молчим: человек мог потерять запомненные голоса
                self.listener.on_error(
                    f"Память голосов повреждена, начал с чистой. Старая копия сохранена: {book.load_error}"
                )
            self._voice_book = book
        return self._voice_book

    def _voicebook(self) -> VoiceBook | None:
        return self._book() if self.settings.remember_voices else None

    def _refine(self, transcript: Transcript, diarizers: dict, bases: dict, save) -> None:  # noqa: ANN001
        """После созвона голоса кластеризуются заново по всем фразам сразу; сбой не должен испортить запись."""
        if not diarizers:
            return
        before = transcript.full_state()
        try:
            result = refine_transcript(transcript, diarizers, bases, self._voicebook())
            save()
        except Exception:
            # откатываем целиком: иначе в памяти были бы новые подписи, а в файле старые
            transcript.restore(before)
            log.exception("Не удалось уточнить голоса")
            self.listener.on_error("Не получилось уточнить голоса, оставил подписи как были по ходу записи")
            return
        book = self._voicebook()
        if book is not None:
            try:
                if remember_names(transcript, book):
                    self.listener.on_status("Голоса знакомых сохранены в памяти")
            except Exception:  # память голосов — приятное дополнение: её сбой не должен портить уже уточнённый транскрипт
                log.exception("Не сохранил память голосов")
                self.listener.on_error("Не получилось сохранить голоса в память")
        if result.changed or result.recognized or result.heard:
            self.listener.on_transcript(transcript, self.last_file or Path())  # лента перерисуется с новыми подписями
            note = f"Голоса уточнены: {result.speakers} собеседн."
            if result.recognized:
                note += f", узнал по голосу: {', '.join(result.recognized)}"
            if result.heard:
                note += f", по разговору: {', '.join(result.heard)}"
            self.listener.on_status(note)

    def rename_speaker(self, voice_id: str, name: str) -> int:
        """Подписывает говорящего именем во всём транскрипте. Возвращает число изменённых реплик."""
        if not self.transcript:
            return 0
        if self.state in (AppState.STOPPING, AppState.FILE, AppState.STARTING):
            # голоса как раз уточняются: имя встало бы между чтением и записью результата и пропало бы
            self.listener.on_error("Голоса ещё уточняются. Подпишите человека через секунду.")
            return 0
        changed = self.transcript.rename_voice(voice_id, name)
        if changed:
            if not self.save_now():
                self.listener.on_error("Имя встало в ленту, но файл сохранить не получилось: проверьте место на диске.")
            if self.session is None and self.file_job is None:  # созвон окончен: сразу запоминаем голос
                book = self._voicebook()
                if book is not None:
                    try:
                        remember_names(self.transcript, book)
                    except Exception:
                        log.exception("Не сохранил память голосов")
                        self.listener.on_error("Имя подписано, но голос в память сохранить не получилось")
        return changed

    def known_voices(self) -> list[str]:
        return self._book().names

    def forget_voices(self) -> int:
        return self._book().forget()

    def cancel_file(self) -> None:
        if self.file_job:
            self.file_job.cancel()
            self.listener.on_status("Отменяю расшифровку…")

    # ---------- транскрипт ----------

    def save_now(self) -> bool:
        """Сохраняет текущий транскрипт. False — не получилось (диск полон, файл занят)."""
        if self.session:
            return self.session.save() is not None
        if self.file_job:
            return self.file_job.saver.save() is not None
        if self.transcript is not None and self.last_file is not None:
            # созвон уже закончен (например, человека подписали после записи): пишем файл сами
            try:
                write_atomic(self.last_file, self.transcript.to_markdown())
            except OSError:
                log.exception("Не сохранил транскрипт %s", self.last_file)
                return False
        return True

    def save_as(self, target: Path) -> str:
        if not self.transcript or not self.transcript.utterances:
            raise ValueError("Пока нечего сохранять")
        text = self.transcript.to_plain_text() if target.suffix.lower() == ".txt" else self.transcript.to_markdown()
        write_atomic(target, text)
        return f"Сохранено: {target}"

    def plain_text(self) -> str:
        return self.transcript.to_plain_text() if self.transcript else ""

    # ---------- итоги на локальной модели ----------

    def summary_model(self) -> summary.LlmModel:
        return summary.CATALOG_BY_KEY.get(self.settings.summary_model) or summary.CATALOG_BY_KEY[summary.DEFAULT_LLM]

    def make_summary(self, on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> str:
        """Итоги текущего разговора (вызывается из фонового потока). SummaryError — с понятным текстом."""
        transcript = self.transcript
        if transcript is None or not transcript.utterances:
            raise summary.SummaryError("Пока не о чем подводить итоги: запишите созвон или расшифруйте файл")
        if self.state != AppState.IDLE or self.busy:
            raise summary.SummaryError("Дождитесь конца записи или расшифровки: итогам нужен весь разговор")
        model = self.summary_model()
        verdict, note = summary.fit(model, summary.total_ram_gb())
        if verdict == "no":
            raise summary.SummaryError(note + ". Выберите модель полегче в настройках, раздел «Итоги»")
        engine = summary.ensure_engine(on_status, cancelled)
        weights = summary.ensure_model(model, on_status, cancelled)
        self.unload_models()  # модели распознавания сейчас не нужны: отдаём их гигабайт модели итогов
        on_status(f"Загружаю {model.title}…")
        server = summary.LlamaServer(engine, weights)
        try:
            server.wait_ready(cancelled=cancelled)
            text = summary.summarize(server, transcript.to_plain_text(), on_status, cancelled)
        finally:
            server.stop()
        if not text:
            raise summary.SummaryError("Модель вернула пустой ответ. Попробуйте ещё раз или выберите другую модель")
        transcript.summary = text
        self.save_now()
        return text

    def open_folder(self) -> None:
        folder = self.settings.transcripts_path
        folder.mkdir(parents=True, exist_ok=True)
        open_path(folder)

    def open_transcript(self) -> bool:
        if self.last_file and self.last_file.exists():
            open_path(self.last_file)
            return True
        return False

    def ai_target(self) -> str:
        """Куда сейчас уйдёт разговор (выбор «сама» уже превращён в конкретную нейросеть)."""
        return ai.resolve(self.settings.ai_target)

    def send_to_ai(self) -> ClaudeResult:
        """Отдаёт разговор выбранной нейросети. Claude Code читает файл сам, веб-чатам — текст через буфер обмена."""
        target = self.ai_target()
        if not self.transcript or not self.transcript.utterances:
            return ClaudeResult(False, "Сначала запишите созвон или расшифруйте файл — отправлять пока нечего.")
        text = ai.chat_text(self.settings.claude_prompt, self.transcript.to_markdown())
        paste = "⌘V" if sys.platform == "darwin" else "Ctrl+V"
        info = ai.TARGETS[target]
        if info.probe:  # зарубежный сервис: сначала убеждаемся, что он доступен и не забанит за страну
            country = self._probe_country(info.probe)
            problem = ai.availability_problem(info, country)
            if not problem and info.reach and not self._reachable(info.reach):
                problem = f"{info.name} не открывается из вашей сети"
            if problem:
                return ClaudeResult(
                    False,
                    f"{problem}. Ничего не открыл и не отправил, разговор скопирован. "
                    + ai.availability_hint(country),
                    clipboard=text,
                )
        desktop = self._send_to_desktop_app(target, text, paste)
        if desktop is not None:
            return desktop
        if target == ai.CLAUDE_CODE:
            return self.send_to_claude()
        if not info.url:
            return ClaudeResult(True, f"Разговор и запрос скопированы — вставьте в любую нейросеть ({paste})", clipboard=text)
        # окно чата появится — Говорилка сама вставит текст, в том числе после входа в аккаунт (qt/autopaste.py)
        message = (f"Открываю {info.name}: разговор вставится в поле ввода сам — и после входа в аккаунт тоже. "
                   f"Проверьте и нажмите Enter. Не вставился — нажмите {paste}")
        return ClaudeResult(True, message, clipboard=text, url=info.url, target=target)

    def _use_desktop(self, key: str) -> bool:
        return self.settings.ai_open_in != "browser" and desktop_apps.installed(key)

    def _send_to_desktop_app(self, target: str, text: str, paste: str) -> ClaudeResult | None:
        """Если у человека установлено приложение ChatGPT или Claude, открываем разговор в нём, а не в браузере.
        None — приложения нет (или выбран браузер): дальше сработает обычный путь."""
        try:
            if target == ai.CLAUDE_CODE and self._use_desktop("claude"):
                return self._claude_code_in_desktop()
            if target == "claude" and self._use_desktop("claude"):
                link = "claude://claude.ai/new?q=" + quote(text, safe="")
                if len(link) <= desktop_apps.MAX_LINK:  # короткий разговор — сразу в поле ввода
                    desktop_apps.open_link(link)
                    return ClaudeResult(True, "Открыл приложение Claude: разговор уже в поле ввода — проверьте и нажмите Enter", clipboard=text)
                desktop_apps.open_link("claude://claude.ai/new")
                return ClaudeResult(True, self._autopaste_message("Claude", paste), clipboard=text, target="claude")
            if target == "chatgpt" and self._use_desktop("chatgpt"):
                desktop_apps.launch("chatgpt")
                return ClaudeResult(True, self._autopaste_message("ChatGPT", paste), clipboard=text, target="chatgpt")
        except OSError as exc:
            log.warning("Не открыл приложение нейросети (%s), открываю обычным путём: %s", target, exc)
            desktop_apps.forget()
        return None

    @staticmethod
    def _autopaste_message(name: str, paste: str) -> str:
        return (f"Открываю приложение {name}: разговор вставится в поле ввода сам — и после входа в аккаунт тоже. "
                f"Проверьте и нажмите Enter. Не вставился — нажмите {paste}")

    def _claude_code_in_desktop(self) -> ClaudeResult:
        """Claude Code во вкладке «Code» приложения Claude: вместо окна терминала."""
        if not self.save_now():
            return ClaudeResult(False, "Не получилось сохранить свежий транскрипт, Claude увидел бы старую версию. "
                                       "Освободите место на диске или смените папку в настройках.")
        if not self.last_file or not self.last_file.exists():
            return ClaudeResult(False, "Сначала запишите созвон или расшифруйте файл — отправлять пока нечего.")
        prompt = build_prompt(self.last_file, self.settings.claude_prompt)
        folder = self.settings.claude_path
        folder.mkdir(parents=True, exist_ok=True)
        link = f"claude://code/new?q={quote(prompt, safe='')}&folder={quote(str(folder), safe='')}"
        desktop_apps.open_link(link)
        return ClaudeResult(
            True,
            "Открыл Claude Code в приложении Claude. Подтвердите папку, если приложение спросит, и нажмите Enter",
            clipboard=prompt,
        )

    def send_to_claude(self) -> ClaudeResult:
        if not self.save_now():
            return ClaudeResult(
                False,
                "Не получилось сохранить свежий транскрипт, Claude увидел бы старую версию. "
                "Освободите место на диске или смените папку в настройках.",
            )
        if not self.last_file or not self.last_file.exists():
            return ClaudeResult(False, "Сначала запишите созвон или расшифруйте файл — отправлять пока нечего.")
        prompt = build_prompt(self.last_file, self.settings.claude_prompt)
        try:
            open_in_claude(self.last_file, self.settings.claude_path, prompt)
        except ClaudeNotFoundError as exc:
            return ClaudeResult(False, str(exc), clipboard=prompt)
        except OSError as exc:
            return ClaudeResult(False, f"Не запустил Claude Code: {exc}")
        return ClaudeResult(True, f"Открыл Claude Code с файлом {self.last_file.name}")

    # ---------- настройки ----------

    def update_settings(self, settings: Settings, autostart_on: bool | None = None) -> list[str]:
        """Сохраняет настройки и применяет сразу. Возвращает ошибки, о которых надо сказать."""
        self.settings = settings
        save_settings(settings)
        self.apply_hotkey()
        problems = []
        if autostart_on is not None:
            try:
                if autostart_on != autostart.is_enabled():
                    autostart.set_enabled(autostart_on)
            except OSError as exc:
                log.exception("Не удалось изменить автозапуск")
                problems.append(f"Не получилось изменить автозапуск: {exc}")
        return problems

    def change_settings(self, **changes: object) -> None:
        self.update_settings(replace(self.settings, **changes))  # type: ignore[arg-type]

    def apply_hotkey(self) -> None:
        self._apply_dictation_hotkey()
        if not self.settings.hotkey_enabled:
            self.hotkeys.set_chord(None)
            return
        try:
            self.hotkeys.set_chord(parse_chord(self.settings.hotkey))
        except ValueError as exc:
            self.hotkeys.set_chord(None)
            self.listener.on_status(f"Горячая клавиша не включена: {exc}")

    def _apply_dictation_hotkey(self) -> None:
        set_dictation = getattr(self.hotkeys, "set_dictation_chord", None)
        if set_dictation is None:  # подменённые в тестах горячие клавиши
            return
        if not self.settings.dictation_enabled:
            set_dictation(None)
            return
        try:
            chord = parse_chord(self.settings.dictation_hotkey)
        except ValueError as exc:
            set_dictation(None)
            self.listener.on_status(f"Диктовка не включена: {exc}")
            return
        if self.settings.hotkey_enabled and self.settings.hotkey == chord_to_string(chord):
            set_dictation(None)
            self.listener.on_status("Диктовка не включена: у неё та же комбинация, что у записи. Задайте другую в настройках")
            return
        set_dictation(chord)

    # ---------- диктовка ----------

    def start_dictation(self) -> None:
        """Зажали комбинацию диктовки (вызывается из потока горячих клавиш)."""
        with self._dictation_lock:
            if self._closing or self._dictation_run is not None or self.busy:
                return
            run = _DictationRun()
            self._dictation_run = run
        self.listener.on_dictation("loading" if self.engines is None else "listening")
        threading.Thread(target=self._open_dictation, args=(run,), name="dictation", daemon=True).start()

    def stop_dictation(self, cancelled: bool = False) -> None:
        """Отпустили комбинацию: дораспознаём и отдаём текст. cancelled — начали печатать: текст не нужен."""
        with self._dictation_lock:
            run = self._dictation_run
            if run is None:
                return
            run.released.set()
            run.cancelled = run.cancelled or cancelled
            ready = run.dictation is not None
        if ready:
            threading.Thread(target=self._finish_dictation, args=(run,), name="dictation-finish", daemon=True).start()

    def _open_dictation(self, run: _DictationRun) -> None:
        try:
            engines = self._ensure_engines()
            dictation = Dictation(
                engines,
                on_level=lambda level: self.listener.on_level("dictation", level),
                on_limit=self.stop_dictation,
            )
            dictation.start()
        except PermissionDeniedError as exc:
            self._end_dictation(run)
            self.listener.on_permission(exc)
            return
        except Exception as exc:  # noqa: BLE001 — нет микрофона, модели не скачались: человек должен узнать
            log.exception("Диктовка не началась")
            self._end_dictation(run)
            self.listener.on_error(f"Диктовка не началась: {exc}")
            return
        with self._dictation_lock:
            run.dictation = dictation
            released = run.released.is_set()
        if released:  # отпустили, пока открывался микрофон
            self._finish_dictation(run)
        else:
            self.listener.on_dictation("listening")

    def _finish_dictation(self, run: _DictationRun) -> None:
        with self._dictation_lock:
            if run.finishing or run.dictation is None:
                return
            run.finishing = True
        if run.cancelled:
            run.dictation.cancel()
            self._end_dictation(run)
            return
        self.listener.on_dictation("recognizing")
        try:
            text = run.dictation.finish()
        except Exception as exc:  # noqa: BLE001
            log.exception("Диктовка не распознана")
            self._end_dictation(run)
            self.listener.on_error(f"Не получилось распознать диктовку: {exc}")
            return
        wait_released = getattr(self.hotkeys, "wait_released", None)
        if wait_released is not None:
            wait_released()
        self._end_dictation(run)
        self.listener.on_dictation_text(text)

    def _end_dictation(self, run: _DictationRun) -> None:
        with self._dictation_lock:
            if self._dictation_run is run:
                self._dictation_run = None
        self.listener.on_dictation("")
        if self.state == AppState.IDLE and self.session is None:
            self._schedule_unload()

    @staticmethod
    def autostart_enabled() -> bool:
        return autostart.is_enabled()

    @staticmethod
    def can_reset_permission() -> bool:
        return permissions.can_reset()

    @staticmethod
    def reset_permission(service: str) -> str | None:
        return permissions.reset_permission(service)


def open_path(path: Path) -> None:
    """Открывает файл или папку программой по умолчанию."""
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        import os

        os.startfile(path)  # noqa: S606 — проводник или программа для .md
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
