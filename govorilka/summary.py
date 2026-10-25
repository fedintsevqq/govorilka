"""Итоги созвона на локальной языковой модели: без интернета и без ключей.

Движок — llama.cpp (llama-server). Он не входит в приложение: при первом использовании скачивается с GitHub
(закреплённая сборка, сверка SHA-256) в папку данных, как и модели распознавания. Модель — файл GGUF с Hugging Face,
тоже со сверкой SHA-256. llama-server запускается отдельным процессом только на время составления итогов и слушает
только 127.0.0.1.

Слабому компьютеру большая модель не по силам: перед выбором смотрим, сколько у компьютера памяти. Если меньше
минимума модели, выбрать её нельзя, а если меньше комфортного — предупреждаем, что будет медленно.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
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
CONTEXT_TOKENS = 16384
CHARS_PER_TOKEN = 3.0  # русский текст в токенизаторах Qwen — примерно 3 символа на токен
CHUNK_CHARS = 24_000  # кусок длинного разговора: ~8 тысяч токенов, с запасом под инструкцию и ответ
START_TIMEOUT_S = 180.0
ANSWER_TOKENS = 1200


class SummaryError(RuntimeError):
    """Итоги не получились: текст показываем человеку."""


@dataclass(frozen=True)
class EngineBuild:
    asset: str
    sha256: str
    binary: str  # где внутри архива llama-server


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
    size_mb: int
    sha256: str
    min_ram_gb: int  # меньше — модель не запустится или вытеснит всё остальное
    comfortable_ram_gb: int  # меньше — работает, но медленно и мешает другим программам
    license: str
    recommended: bool = False

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/main/{self.file}"

    @property
    def path(self) -> Path:
        return LLM_DIR / "models" / self.file


CATALOG: list[LlmModel] = [
    LlmModel(
        "qwen3-4b", "Qwen3 4B", "Хороший баланс: понятные итоги на русском за пару минут на обычном ноутбуке",
        "unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2382,
        "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597", 8, 12, "Apache 2.0", recommended=True,
    ),
    LlmModel(
        "qwen2.5-1.5b", "Qwen2.5 1.5B", "Самая лёгкая: для компьютеров с 6–8 ГБ памяти. Итоги проще и короче",
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf", 1066,
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e", 6, 8, "Apache 2.0",
    ),
    LlmModel(
        "yandexgpt-5-lite-8b", "YandexGPT 5 Lite 8B", "Лучше всех пишет по-русски, но тяжелее и медленнее",
        "yandex/YandexGPT-5-Lite-8B-instruct-GGUF", "YandexGPT-5-Lite-8B-instruct-Q4_K_M.gguf", 4693,
        "d9ff5b826f20fbcc2f898f9f2349ac21241579f8fbb79cd32148a333623ba228", 12, 16, "лицензия Яндекса",
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


def engine_path() -> Path | None:
    build = engine_build()
    if build is None:
        return None
    path = engine_dir() / build.binary
    return path if path.is_file() else None


def is_downloaded(model: LlmModel) -> bool:
    return model.path.is_file() and model.path.stat().st_size > 0


RETRIES = 20  # обрывы связи на многочасовой загрузке — обычное дело: продолжаем с того же места


def download_resumable(url: str, target: Path, size: int, label: str, allowed: tuple[str, ...],
                       on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> None:
    """Качает в target.part и при обрыве продолжает с того же байта (Range). Брошенная загрузка тоже продолжится
    при следующем нажатии: недокачанный .part остаётся на диске."""
    installer._check_url(url, allowed)  # noqa: SLF001 — та же проверка адреса, что у обновлений
    part = target.with_name(target.name + ".part")
    failures = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if size and have >= size:
            break
        request = urllib.request.Request(url, headers={"User-Agent": "Govorilka", **({"Range": f"bytes={have}-"} if have else {})})
        try:
            with urllib.request.urlopen(request, timeout=60, context=net.ssl_context()) as response:  # noqa: S310
                installer._check_url(response.geturl(), allowed)  # noqa: SLF001
                if have and response.status != 206:  # сервер не умеет продолжать: начинаем заново
                    have = 0
                    part.unlink(missing_ok=True)
                total = size or have + int(response.headers.get("Content-Length") or 0)
                last = 0.0
                with part.open("ab") as out:
                    while block := response.read(installer.CHUNK):
                        if cancelled():
                            raise SummaryError("Отменено")
                        out.write(block)
                        have += len(block)
                        if time.monotonic() - last >= 0.5:
                            last = time.monotonic()
                            on_status(models.format_progress(label, have, total or None))
            if not size:
                break
        except (OSError, installer.InstallError) as exc:
            failures += 1
            if failures > RETRIES:
                raise SummaryError(f"Не удалось скачать {label}: {exc}. Скачанное сохранено, нажмите ещё раз — продолжится") from exc
            log.info("Загрузка %s оборвалась (%s), продолжаю с %d байт", label, exc, have)
            time.sleep(min(30, 2 * failures))
    part.replace(target)


def _fetch(url: str, target: Path, size: int, sha256: str, label: str, allowed: tuple[str, ...],
           on_status: Callable[[str], None], cancelled: Callable[[], bool]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    download_resumable(url, target, size, label, allowed, on_status, cancelled)
    on_status(f"Проверяю {label}…")
    if installer.sha256_of(target).lower() != sha256.lower():
        target.unlink(missing_ok=True)
        raise SummaryError(f"Контрольная сумма не совпала: {label} скачался с ошибкой или подменён. Попробуйте ещё раз")


def ensure_engine(on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> Path:
    """llama-server: скачивает и распаковывает один раз. Возвращает путь к программе."""
    existing = engine_path()
    if existing is not None:
        return existing
    build = engine_build()
    if build is None:
        raise SummaryError("Для этого компьютера нет сборки движка итогов")
    work = Path(tempfile.mkdtemp(prefix="govorilka-llama-"))
    try:
        archive = work / build.asset
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_BUILD}/{build.asset}"
        _fetch(url, archive, 0, build.sha256, "движок итогов", GITHUB, on_status, cancelled)
        staging = work / "unpacked"
        if build.asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(staging)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging, filter="data")  # без путей за пределы папки и без чужих владельцев
        target = engine_dir()
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(target))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    path = engine_path()
    if path is None:
        raise SummaryError("В архиве движка не нашлось llama-server")
    if sys.platform != "win32":
        path.chmod(0o755)
    return path


def ensure_model(model: LlmModel, on_status: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> Path:
    if is_downloaded(model):
        return model.path
    _fetch(model.url, model.path, model.size_mb * 1024 * 1024, model.sha256, f"модель {model.title}", HUGGING_FACE,
           on_status, cancelled)
    return model.path


def delete_model(model: LlmModel) -> int:
    """Удаляет файл модели. Возвращает, сколько мегабайт освободилось."""
    if not model.path.exists():
        return 0
    size = model.path.stat().st_size // (1024 * 1024)
    model.path.unlink(missing_ok=True)
    return size


# ---------- llama-server ----------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LlamaServer:
    """llama-server на время составления итогов. Слушает только этот компьютер."""

    def __init__(self, engine: Path, model: Path, threads: int | None = None) -> None:
        self._port = _free_port()
        self._url = f"http://127.0.0.1:{self._port}"
        gpu = ["-ngl", "99"] if sys.platform == "darwin" and platform.machine() == "arm64" else ["-ngl", "0"]
        command = [
            str(engine), "-m", str(model), "--host", "127.0.0.1", "--port", str(self._port),
            "-c", str(CONTEXT_TOKENS), "--threads", str(threads or max(2, (os.cpu_count() or 4) - 1)),
            "--no-webui", *gpu,
        ]
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: без чёрного окна консоли
        self._process = subprocess.Popen(  # noqa: S603 — программа и модель скачаны нами и сверены по SHA-256
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=flags, cwd=str(engine.parent),
        )

    def wait_ready(self, timeout: float = START_TIMEOUT_S, cancelled: Callable[[], bool] = lambda: False) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cancelled():
                raise SummaryError("Отменено")
            if self._process.poll() is not None:
                error = (self._process.stderr.read() if self._process.stderr else b"").decode("utf-8", "replace")
                raise SummaryError(f"Движок итогов не запустился: {error.strip().splitlines()[-1] if error.strip() else 'код ' + str(self._process.returncode)}")
            try:
                with urllib.request.urlopen(f"{self._url}/health", timeout=2) as response:  # noqa: S310 — свой процесс на 127.0.0.1
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
        request = urllib.request.Request(f"{self._url}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=1800) as response:  # noqa: S310 — свой процесс на 127.0.0.1
                data = json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            raise SummaryError(f"Движок итогов не ответил: {exc}") from exc
        return clean_answer(str(data["choices"][0]["message"]["content"]))

    def stop(self) -> None:
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
    "### Решения\nСписок принятых решений.\n"
    "### Задачи\nСписок «кто — что сделать — срок», если срок называли.\n"
    "### Открытые вопросы\nЧто осталось нерешённым.\n"
    "Без вступлений и заключений, только разделы."
)


def clean_answer(text: str) -> str:
    """Убирает рассуждения моделей (<think>…</think>) и лишние пустые строки."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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
