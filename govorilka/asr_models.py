"""Какие модели распознавания речи умеет использовать Говорилка.

Ключ модели — одна строка в настройках (`Settings.model`):

- `gigaam-v3-e2e-rnnt` — готовая модель из каталога (две от Сбера);
- `gigaam-v3-e2e-rnnt@int8` — она же в облегчённом виде int8: файлы в 4 раза меньше (214 МБ вместо 844 МБ),
  памяти нужно на 600–700 МБ меньше;
- `hf:автор/репозиторий` (и `hf:автор/репозиторий@int8`) — своя модель с Hugging Face: скачивается один раз в папку
  с моделями; в репозитории должен быть файл config.json с полем model_type (так устроены репозитории onnx-asr);
- `dir:тип:путь` — своя модель в папке на компьютере; тип — один из CUSTOM_TYPES (`auto` — прочитать model_type
  из config.json в этой папке). Из папки ничего не копируется и не скачивается.

Движок один — onnx-asr: он понимает форматы NeMo (FastConformer, Parakeet, Canary), Kaldi/Vosk, T-One, Whisper
и GigaAM в ONNX. Модель, которой он не знает, не загрузится — об этом приложение скажет понятным текстом.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from govorilka.config import DEFAULT_MODEL, MODELS, MODELS_DIR

log = logging.getLogger(__name__)

INT8 = "int8"
HF_PREFIX, DIR_PREFIX = "hf:", "dir:"
AUTO_TYPE = "auto"

# типы моделей, которые onnx-asr читает из папки: (id, как назвать человеку, какие файлы нужны)
CUSTOM_TYPES: list[tuple[str, str, str]] = [
    (AUTO_TYPE, "Определить по config.json в папке", "config.json с полем model_type"),
    ("nemo-conformer-ctc", "NeMo Conformer CTC (в том числе FastConformer)", "model.onnx, vocab.txt"),
    ("nemo-conformer-rnnt", "NeMo Conformer RNNT", "encoder-model.onnx, decoder_joint-model.onnx, vocab.txt"),
    ("nemo-conformer-tdt", "NeMo Parakeet TDT", "encoder-model.onnx, decoder_joint-model.onnx, vocab.txt"),
    ("nemo-conformer-aed", "NeMo Canary (AED)", "encoder-model.onnx, decoder-model.onnx, vocab.txt"),
    ("kaldi-rnnt", "Vosk / Kaldi (zipformer RNNT)", "encoder.onnx, decoder.onnx, joiner.onnx, tokens.txt"),
    ("t-one-ctc", "T-One CTC", "model.onnx"),
    ("whisper-ort", "Whisper (экспорт onnxruntime)", "whisper-*_beamsearch.onnx, vocab.json, added_tokens.json"),
    ("whisper", "Whisper (экспорт Hugging Face / optimum)", "encoder_model.onnx, decoder_model_merged.onnx, vocab.json"),
    ("gigaam-v3-e2e-rnnt", "GigaAM v3 RNNT (дообученная)", "v3_e2e_rnnt_encoder/decoder/joint.onnx, vocab"),
    ("gigaam-v3-e2e-ctc", "GigaAM v3 CTC (дообученная)", "v3_e2e_ctc.onnx, v3_e2e_ctc_vocab.txt"),
    ("gigaam-v2-ctc", "GigaAM v2/v3 CTC", "v?_ctc.onnx, v?_vocab.txt"),
    ("gigaam-v2-rnnt", "GigaAM v2/v3 RNNT", "v?_rnnt_encoder/decoder/joint.onnx"),
]
KNOWN_TYPES = {name for name, _label, _files in CUSTOM_TYPES if name != AUTO_TYPE}
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")  # автор/репозиторий


class ModelError(ValueError):
    """Ключ модели неверный или файлов модели нет: текст ошибки показываем человеку."""


@dataclass(frozen=True)
class ModelRef:
    key: str  # как хранится в настройках
    label: str  # как показать человеку
    load_name: str  # первый аргумент onnx_asr.load_model / резолвера
    local_dir: Path
    quantization: str | None = None
    local_only: bool = False  # папка человека: ничего не скачиваем
    repo: str = ""  # репозиторий Hugging Face, если модель скачивается


def _split_quantization(text: str) -> tuple[str, str | None]:
    if text.endswith("@" + INT8):
        return text[: -len(INT8) - 1], INT8
    return text, None


def catalog_key(model_id: str, quantization: str | None = None) -> str:
    return f"{model_id}@{quantization}" if quantization else model_id


def hf_key(repo: str, quantization: str | None = None) -> str:
    return HF_PREFIX + catalog_key(repo.strip(), quantization)


def dir_key(model_type: str, path: str | Path) -> str:
    return f"{DIR_PREFIX}{model_type}:{path}"


def is_custom(key: str) -> bool:
    return key.startswith((HF_PREFIX, DIR_PREFIX))



def read_model_type(folder: Path) -> str:
    """Тип модели из config.json в её папке. ModelError, если файла нет или тип незнакомый."""
    config = folder / "config.json"
    if not config.is_file():
        raise ModelError(
            f"В папке нет файла config.json, по нему определяется тип модели. Выберите тип вручную: {folder}"
        )
    try:
        model_type = str(json.loads(config.read_text(encoding="utf-8")).get("model_type", ""))
    except (OSError, ValueError) as exc:
        raise ModelError(f"Не прочитал config.json: {exc}") from exc
    if model_type not in KNOWN_TYPES:
        raise ModelError(
            f"Тип «{model_type or 'не указан'}» из config.json Говорилка не поддерживает или не может определить сама. "
            "Выберите тип модели в списке вручную"
        )
    return model_type


def parse(key: str) -> ModelRef:
    """Разбирает ключ из настроек. ModelError, если ключ не наш."""
    if key.startswith(DIR_PREFIX):
        rest = key[len(DIR_PREFIX):]
        model_type, _, path = rest.partition(":")
        # путь Windows начинается с буквы диска и двоеточия: «dir:auto:E:\папка» — делим только по первому
        if model_type not in KNOWN_TYPES | {AUTO_TYPE} or not path:
            raise ModelError(f"Не понял модель из папки: {key}")
        folder = Path(path)
        resolved = read_model_type(folder) if model_type == AUTO_TYPE and folder.is_dir() else model_type
        if resolved == AUTO_TYPE:
            raise ModelError(f"Папка с моделью не найдена: {folder}")
        return ModelRef(key, f"Своя модель: {folder.name}", resolved, folder, None, local_only=True)
    if key.startswith(HF_PREFIX):
        repo, quantization = _split_quantization(key[len(HF_PREFIX):])
        if not _REPO.match(repo):
            raise ModelError(f"Адрес модели должен быть вида «автор/репозиторий», а не «{repo}»")
        folder = MODELS_DIR / (repo.replace("/", "--") + (f"@{quantization}" if quantization else ""))
        return ModelRef(key, f"Своя модель: {repo}", repo, folder, quantization, repo=repo)
    model_id, quantization = _split_quantization(key)
    if model_id not in MODELS:
        raise ModelError(f"Незнакомая модель: {key}")
    folder = MODELS_DIR / (model_id if not quantization else f"{model_id}@{quantization}")
    return ModelRef(key, MODELS[model_id], model_id, folder, quantization, repo="")


def is_valid(key: str) -> bool:
    """Ключ разобрать можно (папка при этом может ещё не существовать — это проверяется при запуске записи)."""
    try:
        parse(key)
    except ModelError:
        return key.startswith(DIR_PREFIX) and _dir_key_shape(key)
    return True


def _dir_key_shape(key: str) -> bool:
    model_type, _, path = key[len(DIR_PREFIX):].partition(":")
    return model_type in KNOWN_TYPES | {AUTO_TYPE} and bool(path)


def display_name(key: str) -> str:
    try:
        return parse(key).label
    except ModelError:
        return key


def catalog_entries() -> list[dict[str, str]]:
    """Готовые модели для выпадающего списка: две GigaAM и их облегчённые версии."""
    entries = [{"id": key, "label": label} for key, label in MODELS.items()]
    entries += [
        {"id": catalog_key(key, INT8), "label": f"{label.split(' — ')[0]} — экономная int8 (в 4 раза меньше файлы и память)"}
        for key, label in MODELS.items()
    ]
    return entries


def normalize(key: str) -> str:
    """Неизвестное значение (устаревшее, чужое, битое) заменяется моделью по умолчанию."""
    return key if is_valid(key) else DEFAULT_MODEL
