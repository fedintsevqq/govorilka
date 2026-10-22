"""Скачивание моделей с Hugging Face с процентом прогресса.

onnx-asr сам скачивает файлы, но молча. Пока идёт скачивание, отдельный поток
считает, сколько байт уже легло в папку модели, и сообщает процент.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from govorilka.config import MODELS_DIR

# Новый бэкенд Hugging Face (Xet) складывает куски в свой кеш, и процент по папке не посчитать.
# Обычная HTTP-загрузка пишет файл прямо в папку модели. Флаг читается при импорте huggingface_hub,
# поэтому ставим его здесь — этот модуль грузится раньше.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]

VAD_NAME = "silero"
SPEAKER_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
CPU = ["CPUExecutionProvider"]  # на macOS onnx-asr сам включает CoreML, а он падает на GigaAM
POLL_S = 0.5
MB = 1024 * 1024


def local_dir(name: str) -> Path:
    return MODELS_DIR / name.replace("/", "--")


def _resolver(kind: str, name: str, offline: bool) -> Any:  # noqa: ANN401 — внутренний класс onnx-asr
    from onnx_asr.loader import create_asr_resolver, create_se_resolver, create_vad_resolver

    factory = {"asr": create_asr_resolver, "vad": create_vad_resolver, "se": create_se_resolver}[kind]
    return factory(name, local_dir(name), offline=offline)


def is_downloaded(kind: str, name: str) -> bool:
    try:
        _resolver(kind, name, offline=True).resolve_model()
    except (FileNotFoundError, OSError, ValueError):
        return False
    except Exception:  # noqa: BLE001 — onnx-asr бросает свои ошибки «файл не найден»
        return False
    return True


def _expected_bytes(resolver: Any) -> int | None:  # noqa: ANN401
    """Сколько весят файлы модели в репозитории; None — не удалось узнать."""
    try:
        from huggingface_hub import HfApi

        files = list(resolver.model_type._get_model_files(None).values())  # noqa: SLF001
        patterns = ["config.json", "config.yaml", *files, *(f.removeprefix("**/") for f in files)]
        patterns += [str(Path(f).with_suffix(".onnx?data")) for f in files if f.endswith(".onnx")]
        info = HfApi().model_info(resolver.repo_id, files_metadata=True)
        return sum(
            s.size or 0 for s in info.siblings or [] if any(fnmatch.fnmatch(s.rfilename, p) for p in patterns)
        ) or None
    except Exception as exc:  # noqa: BLE001 — процент необязателен, без него просто покажем мегабайты
        log.info("Не узнал размер модели: %s", exc)
        return None


def _folder_bytes(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue  # файл как раз переименовали или удалили
    return total


def format_progress(label: str, done: int, total: int | None) -> str:
    if total:
        pct = min(100, done * 100 // total)
        return f"Скачиваю {label}: {done // MB} из {total // MB} МБ ({pct}%)"
    return f"Скачиваю {label}: {done // MB} МБ…"


def ensure_downloaded(kind: str, name: str, label: str, on_status: StatusCallback) -> None:
    """Скачивает модель, если её ещё нет, и по ходу сообщает процент."""
    if is_downloaded(kind, name):
        return
    resolver = _resolver(kind, name, offline=False)
    folder = local_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    total = _expected_bytes(resolver)
    on_status(format_progress(label, 0, total))
    done = threading.Event()

    def report() -> None:
        while not done.wait(POLL_S):
            on_status(format_progress(label, _folder_bytes(folder), total))

    threading.Thread(target=report, name=f"download-{name}", daemon=True).start()
    try:
        resolver.resolve_model()
    finally:
        done.set()
