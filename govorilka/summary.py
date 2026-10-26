"""Итоги созвона на локальной языковой модели: без интернета и без ключей.

Движок — llama.cpp (llama-server). Он не входит в приложение: при первом использовании скачивается с GitHub
(закреплённая сборка, сверка SHA-256) в папку данных, как и модели распознавания. Модель — файл GGUF с Hugging Face,
тоже со сверкой SHA-256. llama-server запускается отдельным процессом только на время составления итогов и слушает
только 127.0.0.1.

Слабому компьютеру большая модель не по силам: перед выбором смотрим, сколько у компьютера памяти. Если меньше
минимума модели, выбрать её нельзя, а если меньше комфортного — предупреждаем, что будет медленно.
"""

from __future__ import annotations

import atexit
import errno
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from govorilka import installer, models, net
from govorilka.config import DATA_DIR

log = logging.getLogger(__name__)

LLM_DIR = DATA_DIR / "llm"
ENGINE_BUILD = "b11550"  # сборка llama.cpp от 10.10.2026: закреплена, чтобы итоги не ломались от свежих изменений
GITHUB = ("github.com", ".githubusercontent.com")
HUGGING_FACE = ("huggingface.co", ".huggingface.co", ".hf.co")
MODELSCOPE = ("modelscope.cn", ".modelscope.cn")  # запасной источник: те же файлы, часто доступнее из России
MODEL_HOSTS = HUGGING_FACE + MODELSCOPE
CONTEXT_TOKENS = 16384
CHARS_PER_TOKEN = 3.0  # русский текст в токенизаторах Qwen — примерно 3 символа на токен
CHUNK_CHARS = 24_000  # кусок длинного разговора: ~8 тысяч токенов, с запасом под инструкцию и ответ
START_TIMEOUT_S = 180.0
ANSWER_TOKENS = 1200


class SummaryError(RuntimeError):
    """Итоги не получились: текст показываем человеку."""


class SourceGone(SummaryError):
    """По этому адресу файла больше нет (404, 403, 410): пробуем следующий адрес, а не повторяем этот."""


@dataclass(frozen=True)
class EngineBuild:
    asset: str
    sha256: str
    binary: str  # где внутри архива llama-server
    urls: tuple[str, ...] = ()  # откуда качать; пусто — релиз llama.cpp на GitHub


ENGINES = {
    ("win32", "x64"): EngineBuild(
        f"llama-{ENGINE_BUILD}-bin-win-cpu-x64.zip",
        "e61c4aa5dd87f62c081d941115725e5de26f04ed04e07dd2a5f30e697767dcab",
        "llama-server.exe",
    ),
    ("darwin", "arm64"): EngineBuild(
        f"llama-{ENGINE_BUILD}-bin-macos-arm64.tar.gz",
        "3dcbbba52d493f3875d86602a273f8ef742e1b411295ceb1e9c2de80aa80950b",
        f"llama-{ENGINE_BUILD}/llama-server",
    ),
    ("darwin", "x64"): EngineBuild(
        f"llama-{ENGINE_BUILD}-bin-macos-x64.tar.gz",
        "48a3f733cd19b1b620997656ea77c109176aa5605b7d98ef26d81d11989001cb",
        f"llama-{ENGINE_BUILD}/llama-server",
    ),
}


@dataclass(frozen=True)
class LlmModel:
    key: str
    title: str
    blurb: str
    repo: str
    file: str
    size: int  # байт, точно как на Hugging Face: по нему видно, что файл докачан
    sha256: str
    min_ram_gb: int  # меньше — модель не запустится или вытеснит всё остальное
    comfortable_ram_gb: int  # меньше — работает, но медленно и мешает другим программам
    license: str
    recommended: bool = False
    mirrors: tuple[str, ...] = ()  # запасные адреса того же файла: если основной пропал, качаем отсюда
    revision: str = "main"  # коммит репозитория: автор может перезалить файл в main, а закреплённый не изменится

    @property
    def size_mb(self) -> int:
        return round(self.size / (1024 * 1024))

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}" if self.repo else ""

    @property
    def urls(self) -> tuple[str, ...]:
        """Все адреса по порядку: Hugging Face, потом зеркала."""
        return tuple(u for u in (self.url, *self.mirrors) if u)

    @property
    def path(self) -> Path:
        return LLM_DIR / "models" / self.file


CATALOG: list[LlmModel] = [
    LlmModel(
        "qwen3-4b", "Qwen3 4B", "Хороший баланс: понятные итоги на русском за пару минут на обычном ноутбуке",
        "unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2_497_281_120,
        "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597", 8, 12, "Apache 2.0", recommended=True,
        mirrors=("https://modelscope.cn/models/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/master/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",),
        revision="a06e946bb6b655725eafa393f4a9745d460374c9",
    ),
    LlmModel(
        "qwen2.5-1.5b", "Qwen2.5 1.5B", "Самая лёгкая: для компьютеров с 6–8 ГБ памяти. Итоги проще и короче",
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf", 1_117_320_736,
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e", 6, 8, "Apache 2.0",
        mirrors=("https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf",),
        revision="91cad51170dc346986eccefdc2dd33a9da36ead9",
    ),
    LlmModel(
        "yandexgpt-5-lite-8b", "YandexGPT 5 Lite 8B", "Лучше всех пишет по-русски, но тяжелее и медленнее",
        "yandex/YandexGPT-5-Lite-8B-instruct-GGUF", "YandexGPT-5-Lite-8B-instruct-Q4_K_M.gguf", 4_920_741_184,
        "d9ff5b826f20fbcc2f898f9f2349ac21241579f8fbb79cd32148a333623ba228", 12, 16, "лицензия Яндекса",
        mirrors=("https://modelscope.cn/models/yandex/YandexGPT-5-Lite-8B-instruct-GGUF/resolve/master/YandexGPT-5-Lite-8B-instruct-Q4_K_M.gguf",),
        revision="9fe287d2f512503046bb008aed350f2b4bbb903d",
    ),
]
CATALOG_BY_KEY = {model.key: model for model in CATALOG}
DEFAULT_LLM = "qwen3-4b"


# ---------- память компьютера ----------


def total_ram_gb() -> float:
    """Сколько оперативной памяти у компьютера, ГБ (0 — не удалось узнать)."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_avail", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_avail", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return status.total / 1024**3
            return 0.0
        if sys.platform == "darwin":
            out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5, check=False)
            return int(out.stdout.strip()) / 1024**3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except Exception as exc:  # noqa: BLE001 — не узнали: не мешаем выбору, просто не предупреждаем
        log.info("Не узнал объём памяти: %s", exc)
        return 0.0


def fit(model: LlmModel, ram_gb: float) -> tuple[str, str]:
    """Подходит ли модель компьютеру: ("ok" | "slow" | "no", пояснение для человека)."""
    if ram_gb <= 0:
        return "ok", ""
    have = round(ram_gb)
    if ram_gb + 0.5 < model.min_ram_gb:
        return "no", f"На этом компьютере {have} ГБ памяти, а модели нужно не меньше {model.min_ram_gb} ГБ: она не запустится"
    if ram_gb + 0.5 < model.comfortable_ram_gb:
        return "slow", f"На этом компьютере {have} ГБ памяти: модель запустится, но итоги будут считаться медленно, а другие программы — тормозить"
    return "ok", ""


# ---------- скачивание ----------


def engine_build() -> EngineBuild | None:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return ENGINES.get((sys.platform, arch))


def engine_dir() -> Path:
    return LLM_DIR / f"llama-{ENGINE_BUILD}"


ENGINE_DONE = ".complete"  # метка «движок распакован целиком»: появляется последней


def engine_path() -> Path | None:
    build = engine_build()
    if build is None:
        return None
    path = engine_dir() / build.binary
    return path if path.is_file() and (engine_dir() / ENGINE_DONE).is_file() else None


_ENGINE_LOCK = threading.Lock()  # движок качают итоги, подсказки и кнопка «Скачать»: распаковывает только один


def is_downloaded(model: LlmModel) -> bool:
    return model.path.is_file() and model.path.stat().st_size == model.size


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """Один замок на файл: модель качают кнопка «Скачать», «Сделать итоги» и подсказки в звонке. Без замка два потока
    дописывали в один .part вперемешку, и файл не сходился по SHA-256."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path.resolve(), threading.Lock())


RETRIES = 20  # обрывы связи на многочасовой загрузке — обычное дело: продолжаем с того же места


class DiskProblem(SummaryError):
    """Не хватает места или нет прав на запись: другой адрес не поможет, повторять бессмысленно."""


DISK_MARGIN = 200 * 1024 * 1024  # запас свободного места сверх файла
_DISK_ERRNOS = (errno.ENOSPC, errno.EACCES, errno.EROFS, errno.EDQUOT if hasattr(errno, "EDQUOT") else errno.ENOSPC)


def _gb(size: int) -> str:
    return f"{size / 1024**3:.1f}".replace(".", ",")


def _check_space(folder: Path, need: int, label: str) -> None:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < need + DISK_MARGIN:
        raise DiskProblem(f"Не хватает места на диске для {label}: нужно {_gb(need + DISK_MARGIN)} ГБ, "
                          f"свободно {_gb(free)} ГБ. Освободите место и нажмите ещё раз")


def _is_disk_error(exc: OSError) -> bool:
    return exc.errno in _DISK_ERRNOS or getattr(exc, "winerror", None) in (39, 112)  # Windows: диск заполнен


def _content_range(response) -> tuple[int, int] | None:  # noqa: ANN001
    """«bytes 100-199/1000» → (100, 1000)."""
    match = re.match(r"bytes (\d+)-\d+/(\d+)", response.headers.get("Content-Range") or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _replace_retrying(part: Path, target: Path) -> None:
    """Windows: антивирус проверяет только что закрытый файл и несколько секунд держит его — ждём, а не падаем."""
    for attempt in range(60):
        try:
            part.replace(target)
            return
        except PermissionError:
            if attempt == 59:
                raise
            time.sleep(0.5)


def download_resumable(url: str, target: Path, size: int, label: str, allowed: tuple[str, ...],
                       on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False,
                       sha256: str = "") -> None:
    """Качает в target.part и при обрыве продолжает с того же байта (Range). Брошенная загрузка тоже продолжится
    при следующем нажатии: недокачанный .part остаётся на диске.

    size — точный размер файла (0 — узнать из ответа сервера). Сервер отдаёт файл другого размера — значит, по этому
    адресу уже другой файл: SourceGone, и качаем с запасного адреса. sha256 сверяется у .part до переименования."""
    installer._check_url(url, allowed)  # noqa: SLF001 — та же проверка адреса, что у обновлений
    part = target.with_name(target.name + ".part")
    failures = 0

    def fail(exc: Exception, message: str) -> None:
        nonlocal failures
        failures += 1
        if failures > RETRIES:
            raise SummaryError(f"Не удалось скачать {label}: {exc}. Скачанное сохранено, нажмите ещё раз — продолжится") from exc
        log.info("Загрузка %s: %s (%s), попытка %d", label, message, exc, failures)
        on_status(f"{message}, продолжаю {label}… (попытка {failures} из {RETRIES})")
        _pause(failures, cancelled)

    while True:
        if cancelled():
            raise SummaryError("Отменено")
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:  # больше, чем весит файл: испорчен (так бывало при одновременной загрузке) — заново
            log.warning("Недокачанный %s больше ожидаемого (%d > %d): качаю заново", label, have, size)
            part.unlink()
            continue
        if size and have == size:
            break
        if size:
            _check_space(part.parent, size - have, label)
        request = urllib.request.Request(url, headers={"User-Agent": "Govorilka", **({"Range": f"bytes={have}-"} if have else {})})
        before = have
        try:
            with urllib.request.urlopen(request, timeout=60, context=net.ssl_context()) as response:  # noqa: S310
                installer._check_url(response.geturl(), allowed)  # noqa: SLF001
                if response.status == 206:
                    span = _content_range(response)
                    if span is None or span[0] != have:  # сервер прислал не тот кусок: начинаем заново
                        part.unlink(missing_ok=True)
                        raise OSError(f"сервер прислал не тот кусок файла ({response.headers.get('Content-Range')})")
                    total = span[1]
                else:
                    if have:  # сервер не продолжает с места обрыва (или это страница прокси): начинаем заново
                        part.unlink(missing_ok=True)
                        have = before = 0
                    total = int(response.headers.get("Content-Length") or 0)
                if size and total and total != size:
                    raise SourceGone(f"по адресу {url.split('/')[2]} лежит другой файл ({total} байт вместо {size})")
                size = size or total
                last = 0.0
                with part.open("ab") as out:
                    while block := response.read(installer.CHUNK):
                        if cancelled():
                            raise SummaryError("Отменено")
                        if size and have + len(block) > size:
                            block = block[: size - have]  # лишнего не пишем
                        out.write(block)
                        have += len(block)
                        if time.monotonic() - last >= 0.5:
                            last = time.monotonic()
                            on_status(models.format_progress(label, have, size or None))
                        if size and have >= size:
                            break
            if not size:
                break  # размер неизвестен, и сервер закрыл соединение сам: считаем, что скачали; дальше SHA-256
            if have == before:  # ни байта за попытку: не крутимся впустую, ждём как при обрыве
                fail(OSError("сервер не отдал данных"), "Сервер не отдаёт данные")
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:  # «дальше ничего нет»: файл уже скачан целиком, дальше его сверит SHA-256
                break
            if exc.code in (401, 403, 404, 410, 451):  # файла по этому адресу нет: повторять бессмысленно
                raise SourceGone(f"{label} недоступна по адресу {url.split('/')[2]} (ответ {exc.code})") from exc
            fail(exc, f"Сервер ответил {exc.code}")
        except OSError as exc:
            if _is_disk_error(exc):
                raise DiskProblem(f"Не получается записать {label} на диск: {exc}. Освободите место или проверьте права на папку") from exc
            fail(exc, "Связь оборвалась")
        except installer.InstallError as exc:
            fail(exc, "Связь оборвалась")
    if sha256:  # сверяем ещё .part: если программу закроют во время проверки, непроверенный файл не останется на месте
        on_status(f"Проверяю {label}…")
        if installer.sha256_of(part).lower() != sha256.lower():
            part.unlink(missing_ok=True)
            raise SummaryError(f"Контрольная сумма не совпала: {label} скачался с ошибкой. Нажмите ещё раз — скачается заново")
    _replace_retrying(part, target)


def _pause(failures: int, cancelled: Callable[[], bool]) -> None:
    """Пауза перед повтором, которую можно прервать кнопкой «Отменить»."""
    end = time.monotonic() + min(30, 2 * failures)
    while time.monotonic() < end:
        if cancelled():
            raise SummaryError("Отменено")
        time.sleep(0.2)


def _fetch(url: str, target: Path, size: int, sha256: str, label: str, allowed: tuple[str, ...],
           on_status: Callable[[str], None], cancelled: Callable[[], bool]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(target)
    if not lock.acquire(blocking=False):
        on_status(f"{label[:1].upper()}{label[1:]} уже качается, жду…")
        while not lock.acquire(timeout=0.5):
            if cancelled():
                raise SummaryError("Отменено")
    try:
        if target.is_file() and installer.sha256_of(target).lower() == sha256.lower():
            return  # пока ждали, файл докачал другой поток
        download_resumable(url, target, size, label, allowed, on_status, cancelled, sha256)
    finally:
        lock.release()


def ensure_engine(on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> Path:
    """llama-server: скачивает и распаковывает один раз. Возвращает путь к программе.

    Распаковка идёт в соседнюю папку и встаёт на место одним переименованием, метка готовности пишется последней:
    прерванная установка не оставит полупустую папку, которую потом принимали бы за готовый движок."""
    existing = engine_path()
    if existing is not None:
        return existing
    build = engine_build()
    if build is None:
        raise SummaryError("Для этого компьютера нет сборки движка итогов")
    if not _ENGINE_LOCK.acquire(blocking=False):
        on_status("Движок итогов уже качается, жду…")
        while not _ENGINE_LOCK.acquire(timeout=0.5):
            if cancelled():
                raise SummaryError("Отменено")
    try:
        existing = engine_path()  # пока ждали, его поставил другой поток
        if existing is not None:
            return existing
        return _install_engine(build, on_status, cancelled)
    finally:
        _ENGINE_LOCK.release()


def _install_engine(build: EngineBuild, on_status: Callable[[str], None], cancelled: Callable[[], bool]) -> Path:
    LLM_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="engine-", dir=LLM_DIR))  # тот же диск: переименование мгновенное
    try:
        archive = work / build.asset
        urls = build.urls or (f"https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_BUILD}/{build.asset}",)
        for index, url in enumerate(urls):
            try:
                _fetch(url, archive, 0, build.sha256, "движок итогов", GITHUB + MODEL_HOSTS, on_status, cancelled)
                break
            except SummaryError as exc:
                if cancelled() or str(exc) == "Отменено" or isinstance(exc, DiskProblem) or index == len(urls) - 1:
                    raise
                log.warning("Движок итогов не скачался с %s: %s", url, exc)
                archive.with_name(archive.name + ".part").unlink(missing_ok=True)
        if cancelled():
            raise SummaryError("Отменено")
        on_status("Распаковываю движок итогов…")
        staging = work / "unpacked"
        if build.asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(staging)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging, filter="data")  # без путей за пределы папки и без чужих владельцев
        binary = staging / build.binary
        if not binary.is_file():
            raise SummaryError("В архиве движка не нашлось llama-server")
        if sys.platform != "win32":
            binary.chmod(0o755)
        (staging / ENGINE_DONE).write_text(build.sha256, encoding="utf-8")
        target = engine_dir()
        if target.exists():  # недоделанная прошлая установка (метки нет): убираем, иначе новая ляжет внутрь
            shutil.rmtree(target)
        os.replace(staging, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _remove_old_engines()
    path = engine_path()
    if path is None:
        raise SummaryError("Движок итогов не установился: попробуйте ещё раз")
    return path


def _remove_old_engines() -> None:
    """Прежние сборки движка после обновления каталога больше не нужны: по 30–80 МБ каждая."""
    for folder in LLM_DIR.glob("llama-b*"):
        if folder.is_dir() and folder != engine_dir():
            shutil.rmtree(folder, ignore_errors=True)
    for folder in LLM_DIR.glob("engine-*"):  # остатки распаковки, прерванной выходом из программы
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def ensure_model(model: LlmModel, on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Качает модель с первого живого адреса: Hugging Face, потом зеркала. Недокачанное продолжается и с другого
    адреса — файл тот же, это проверяет SHA-256."""
    if is_downloaded(model):
        return model.path
    problems = []
    for url in model.urls:
        try:
            _fetch(url, model.path, model.size, model.sha256, f"модель {model.title}", MODEL_HOSTS, on_status, cancelled)
            return model.path
        except SummaryError as exc:
            if cancelled() or str(exc) == "Отменено" or isinstance(exc, DiskProblem):
                raise
            log.warning("Модель %s не скачалась с %s: %s", model.key, url, exc)
            problems.append(str(exc))
    raise SummaryError(problems[-1] if len(problems) == 1 else
                       f"Модель {model.title} не скачалась ни с одного адреса. Последняя ошибка: {problems[-1]}")


def delete_model(model: LlmModel) -> int:
    """Удаляет файл модели и недокачанный остаток. Возвращает, сколько мегабайт освободилось."""
    freed = 0
    for path in (model.path, model.path.with_name(model.path.name + ".part")):
        if path.exists():
            freed += path.stat().st_size
            path.unlink()
    return freed // (1024 * 1024)


# ---------- llama-server ----------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


_RUNNING: set[LlamaServer] = set()  # живые llama-server: при выходе из Говорилки гасим все
_RUNNING_LOCK = threading.Lock()


def stop_all() -> None:
    """Гасит все запущенные llama-server: при выходе и перед установкой обновления. Модель держит гигабайты памяти,
    и процесс не должен пережить Говорилку."""
    with _RUNNING_LOCK:
        servers = list(_RUNNING)
    for server in servers:
        server.stop()


atexit.register(stop_all)


def _kill_with_parent(process: subprocess.Popen) -> None:
    """Windows: процесс в «задании», которое закрывается вместе с Говорилкой, даже если она упала. Иначе модель
    осталась бы в памяти, а файл модели — занят (не удалить)."""
    if sys.platform != "win32":
        return
    global _JOB
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if _JOB is None:
            job = kernel32.CreateJobObjectW(None, None)
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error())
            _JOB = job  # описатель не закрываем: его закроет система, когда Говорилка завершится
        if not kernel32.AssignProcessToJobObject(_JOB, int(process._handle)):  # noqa: SLF001
            raise OSError(ctypes.get_last_error())
    except Exception:  # noqa: BLE001 — без задания процесс всё равно гасится при обычном выходе
        log.exception("Не привязал llama-server к Говорилке")


_JOB = None


class LlamaServer:
    """llama-server на время составления итогов или подсказок. Слушает только этот компьютер и отвечает только
    с ключом, который знает Говорилка: другие программы на компьютере не прочитают через него разговор."""

    def __init__(self, engine: Path, model: Path, threads: int | None = None) -> None:
        self._port = _free_port()
        self._url = f"http://127.0.0.1:{self._port}"
        self.api_key = secrets.token_urlsafe(24)
        # Apple Silicon считает на видеоядре (Metal). GOVORILKA_LLM_GPU=0 — только процессор: в виртуальных машинах
        # (сборка в CI) Metal выдаёт бессмыслицу
        metal = sys.platform == "darwin" and platform.machine() == "arm64" and os.environ.get("GOVORILKA_LLM_GPU") != "0"
        gpu = ["-ngl", "99" if metal else "0"]
        command = [
            str(engine), "-m", str(model), "--host", "127.0.0.1", "--port", str(self._port),
            "-c", str(CONTEXT_TOKENS), "--threads", str(threads or max(2, (os.cpu_count() or 4) - 1)),
            "--no-webui", "--api-key", self.api_key, *gpu,
        ]
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: без чёрного окна консоли
        # журнал движка — в файл: канал, который никто не читает, переполнился бы, и движок встал бы
        self._log_path = LLM_DIR / "llama-server.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = self._log_path.open("wb")
        try:
            self._process = subprocess.Popen(  # noqa: S603 — программа и модель скачаны нами и сверены по SHA-256
                command, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                creationflags=flags, cwd=str(engine.parent),
            )
        finally:
            log_file.close()  # у процесса своя копия
        _kill_with_parent(self._process)
        with _RUNNING_LOCK:
            _RUNNING.add(self)

    @property
    def url(self) -> str:
        return self._url

    def _last_log_line(self) -> str:
        try:
            lines = self._log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        return lines[-1] if lines else ""

    def wait_ready(self, timeout: float = START_TIMEOUT_S, cancelled: Callable[[], bool] = lambda: False) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cancelled():
                raise SummaryError("Отменено")
            if self._process.poll() is not None:
                last = self._last_log_line()
                raise SummaryError(f"Движок итогов не запустился: {last or 'код ' + str(self._process.returncode)}")
            try:
                with net.local_opener().open(f"{self._url}/health", timeout=2) as response:  # свой процесс на 127.0.0.1
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.5)
        raise SummaryError("Модель слишком долго загружается: возможно, не хватает памяти")

    def chat(self, system: str, user: str, max_tokens: int = ANSWER_TOKENS) -> str:
        body = json.dumps({
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2, "max_tokens": max_tokens, "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(f"{self._url}/v1/chat/completions", data=body,
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with net.local_opener().open(request, timeout=1800) as response:  # свой процесс на 127.0.0.1
                data = json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            raise SummaryError(f"Движок итогов не ответил: {exc}") from exc
        return clean_answer(str(data["choices"][0]["message"]["content"]))

    def stop(self) -> None:
        with _RUNNING_LOCK:
            _RUNNING.discard(self)
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()


# ---------- итоги ----------

SYSTEM = (
    "Ты помощник, который пишет итоги рабочих созвонов на русском языке. Пиши только то, что есть в разговоре: "
    "ничего не придумывай, не добавляй сроков и имён, которых не называли. Пиши коротко и по делу."
)
FORMAT = (
    "Составь итоги в Markdown строго по разделам, пропуская пустые:\n"
    "### Кратко\n2–4 предложения: о чём говорили и к чему пришли.\n"
    "### Решения\nТолько то, о чём прямо договорились («решили», «принято», «договорились»).\n"
    "### Задачи\nОтдельный пункт на каждое обещание или поручение: «Кто — что сделать — срок». Кто — тот, кто взял на себя "
    "(«я сделаю», «возьму») или кому поручили по имени; «Я» и «Собеседник 1» пиши как в разговоре. Срок пиши, только если "
    "его назвали.\n"
    "### Открытые вопросы\nТолько вопросы, на которые в разговоре не ответили. Если таких нет, раздел пропусти.\n"
    "Без вступлений и заключений, только разделы. Не выдумывай то, чего не говорили."
)


def clean_answer(text: str) -> str:
    """Убирает рассуждения моделей (<think>…</think>) и лишние пустые строки."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # раздел, в котором модель написала «Нет» или «—», не показываем: пустой раздел только мешает
    text = re.sub(r"(?m)^#{2,4} [^\n]+\n\s*(?:нет|—|-|не указано)\.?\s*(?=\n#|\Z)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def safe_markdown(text: str) -> str:
    """Текст нейросети для показа в окне: без картинок, ссылок и HTML. Ответ из интернета или итоги по чужим словам не
    должны ничего подгружать из сети и не должны вести по ссылке, которую подсказал собеседник."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # картинка → её подпись
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # ссылка → её текст
    text = re.sub(r"</?[A-Za-z][^>\n]{0,200}>", "", text)  # теги HTML; «3 < 5 > 2» не трогаем
    return re.sub(r"(?i)\b(?:https?|file|ftp)://", "", text)  # голые адреса Markdown тоже делает ссылками


def split_transcript(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Длинный разговор — на куски по строкам: реплика не разрывается."""
    chunks, current, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def summarize(server: LlamaServer, transcript: str, on_status: Callable[[str], None] = lambda _: None,
              cancelled: Callable[[], bool] = lambda: False) -> str:
    """Итоги разговора. Длинный разговор сначала сжимается по частям в заметки, потом из заметок — итоги."""
    chunks = split_transcript(transcript)
    if len(chunks) == 1:
        on_status("Составляю итоги…")
        return clean_answer(server.chat(SYSTEM, f"Разговор:\n\n{transcript}\n\n{FORMAT}"))
    notes = []
    for number, chunk in enumerate(chunks, 1):
        if cancelled():
            raise SummaryError("Отменено")
        on_status(f"Читаю разговор: часть {number} из {len(chunks)}…")
        notes.append(clean_answer(server.chat(
            SYSTEM,
            f"Это часть {number} из {len(chunks)} длинного разговора:\n\n{chunk}\n\n"
            "Выпиши подробные заметки: о чём говорили, какие решения приняли, кто что обещал сделать и к какому сроку, "
            "какие вопросы остались. Только факты из этой части.",
            max_tokens=800,
        )))
    on_status("Составляю итоги…")
    joined = "\n\n".join(f"Заметки по части {i}:\n{note}" for i, note in enumerate(notes, 1))
    return clean_answer(server.chat(SYSTEM, f"Заметки по всему разговору:\n\n{joined}\n\n{FORMAT}"))
