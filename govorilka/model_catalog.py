"""Каталог моделей итогов и подсказок по сети: если ссылка на модель умерла, её меняют в репозитории, а не выпускают
новую версию Говорилки.

Файл catalog/models.json лежит в репозитории на GitHub. При запуске Говорилка тихо скачивает его (несколько
килобайт), проверяет и запоминает в папке данных; без сети работает последний сохранённый, а без него — встроенный
каталог из summary.py. Каталог не может подменить файл модели незаметно: у каждой записи своя SHA-256, и скачанный
файл сверяется с ней. Адреса — только Hugging Face, ModelScope и GitHub.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from govorilka import installer, net, summary

log = logging.getLogger(__name__)

CATALOG_URL = "https://raw.githubusercontent.com/fedintsevqq/govorilka/main/catalog/models.json"
FORMAT = 1  # каталог другого формата (из будущей версии) не применяем
TIMEOUT_S = 8
MAX_BYTES = 256 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{1,60}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.gguf$")
_BUILD = re.compile(r"^b\d{3,6}$")

BUILTIN_LLM = list(summary.CATALOG)
BUILTIN_ENGINES = dict(summary.ENGINES)
BUILTIN_ENGINE_BUILD = summary.ENGINE_BUILD


class CatalogError(ValueError):
    """Каталог из сети не прошёл проверку: работаем с прежним."""


def cache_path() -> Path:
    return summary.LLM_DIR / "catalog.json"


def _allowed_url(url: object, hosts: tuple[str, ...]) -> str:
    if not isinstance(url, str) or urlparse(url).scheme != "https":
        raise CatalogError(f"адрес не https: {url!r}")
    host = urlparse(url).hostname or ""
    if not any(host == h or (h.startswith(".") and host.endswith(h)) for h in hosts):
        raise CatalogError(f"чужой адрес: {host}")
    return url


def _model(entry: dict[str, Any]) -> summary.LlmModel:
    key, file, sha = entry.get("key"), entry.get("file"), str(entry.get("sha256", "")).lower()
    if not isinstance(key, str) or not _KEY.match(key):
        raise CatalogError(f"плохой ключ модели: {key!r}")
    if not isinstance(file, str) or not _FILE.match(file):  # имя файла без папок: в чужую папку не запишет
        raise CatalogError(f"плохое имя файла: {file!r}")
    if not _SHA.match(sha):
        raise CatalogError(f"{key}: нет SHA-256")
    size, low, high = entry.get("size"), entry.get("min_ram_gb"), entry.get("comfortable_ram_gb")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (size, low, high)):
        raise CatalogError(f"{key}: размер и память — целые числа")
    urls = entry.get("urls")
    if not isinstance(urls, list) or not urls:
        raise CatalogError(f"{key}: нет адресов")
    return summary.LlmModel(
        key, str(entry.get("title") or key), str(entry.get("blurb") or ""), "", file, size, sha, low, high,
        str(entry.get("license") or ""), bool(entry.get("recommended")),
        tuple(_allowed_url(u, summary.MODEL_HOSTS) for u in urls),
    )


def _engines(data: dict[str, Any]) -> tuple[str, dict[tuple[str, str], summary.EngineBuild]]:
    build = data.get("build")
    if not isinstance(build, str) or not _BUILD.match(build):
        raise CatalogError(f"плохая сборка движка: {build!r}")
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        raise CatalogError("у движка нет списка платформ")
    found = {}
    for name, item in platforms.items():
        system, _, arch = str(name).partition("-")
        if system not in ("win32", "darwin") or arch not in ("x64", "arm64") or not isinstance(item, dict):
            raise CatalogError(f"неизвестная платформа движка: {name}")
        sha, asset, binary = str(item.get("sha256", "")).lower(), item.get("asset"), item.get("binary")
        if (not _SHA.match(sha) or not isinstance(asset, str) or "/" in asset or "\\" in asset
                or not isinstance(binary, str) or ".." in binary or binary.startswith(("/", "\\"))):
            raise CatalogError(f"движок {name}: нет SHA-256, архива или пути к программе")
        urls = tuple(_allowed_url(u, summary.GITHUB + summary.MODEL_HOSTS) for u in item.get("urls") or [])
        found[(system, arch)] = summary.EngineBuild(asset, sha, binary, urls)
    return build, found


def parse(data: object) -> tuple[list[summary.LlmModel], tuple[str, dict] | None]:
    """Проверяет каталог целиком: одна плохая запись — весь каталог не применяется."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise CatalogError("неизвестный формат каталога")
    entries = data.get("llm") or []
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise CatalogError("список моделей испорчен")
    llm = [_model(entry) for entry in entries]
    if len({m.key for m in llm}) != len(llm):
        raise CatalogError("ключи моделей повторяются")
    engine = _engines(data["engine"]) if isinstance(data.get("engine"), dict) else None
    return llm, engine


def merge(builtin: list[summary.LlmModel], remote: list[summary.LlmModel]) -> list[summary.LlmModel]:
    """Записи из сети заменяют встроенные с тем же ключом; новые встают в конец. Если файл тот же (SHA-256 совпала),
    встроенные адреса остаются запасными. Описание и требования к памяти берутся из сети."""
    by_key = {m.key: m for m in remote}
    merged = []
    for model in builtin:
        fresh = by_key.pop(model.key, None)
        if fresh is None:
            merged.append(model)
            continue
        if fresh.sha256 == model.sha256:
            extra = tuple(u for u in model.urls if u not in fresh.urls)
            fresh = replace(fresh, mirrors=fresh.mirrors + extra)
        merged.append(fresh)
    return merged + list(by_key.values())


def apply(data: object) -> None:
    """Применяет проверенный каталог поверх встроенного. Списки в summary меняются на месте: другие модули держат
    ссылки на них."""
    llm, engine = parse(data)
    summary.CATALOG[:] = merge(BUILTIN_LLM, llm)
    summary.CATALOG_BY_KEY.clear()
    summary.CATALOG_BY_KEY.update({m.key: m for m in summary.CATALOG})
    summary.ENGINES.clear()
    if engine is not None and engine[1]:
        summary.ENGINE_BUILD = engine[0]
        summary.ENGINES.update(engine[1])
    else:
        summary.ENGINE_BUILD = BUILTIN_ENGINE_BUILD
        summary.ENGINES.update(BUILTIN_ENGINES)


def _state() -> tuple:
    return list(summary.CATALOG), summary.ENGINE_BUILD, dict(summary.ENGINES)


def load_cached() -> None:
    """При запуске: последний каталог из сети, если он есть и цел."""
    try:
        apply(json.loads(cache_path().read_text(encoding="utf-8")))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        log.warning("Сохранённый каталог моделей не подошёл, беру встроенный: %s", exc)


def refresh(url: str = CATALOG_URL) -> bool:
    """Скачивает свежий каталог. True — что-то поменялось. Без сети или с плохим каталогом — False, прежний остаётся."""
    before = _state()
    try:
        installer._check_url(url, summary.GITHUB)  # noqa: SLF001
        request = urllib.request.Request(url, headers={"User-Agent": "Govorilka"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=net.ssl_context()) as response:  # noqa: S310
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise CatalogError("каталог слишком большой")
        data = json.loads(raw.decode("utf-8"))
        apply(data)
    except (OSError, ValueError, installer.InstallError) as exc:
        log.info("Каталог моделей из сети не обновлён: %s", exc)
        return False
    try:
        cache_path().parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path().with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(cache_path())
    except OSError as exc:  # не сохранили — в этот раз каталог всё равно применён
        log.warning("Не сохранил каталог моделей: %s", exc)
    return _state() != before


def builtin_document() -> dict[str, Any]:
    """Встроенный каталог в формате файла: из него собирается catalog/models.json (tools/check_models.py --write)."""
    return {
        "format": FORMAT,
        "llm": [
            {"key": m.key, "title": m.title, "blurb": m.blurb, "file": m.file, "size": m.size, "sha256": m.sha256,
             "min_ram_gb": m.min_ram_gb, "comfortable_ram_gb": m.comfortable_ram_gb, "license": m.license,
             "recommended": m.recommended, "urls": list(m.urls)}
            for m in BUILTIN_LLM
        ],
        "engine": {
            "build": BUILTIN_ENGINE_BUILD,
            "platforms": {
                f"{system}-{arch}": {
                    "asset": b.asset, "sha256": b.sha256, "binary": b.binary,
                    "urls": list(b.urls) or [f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILTIN_ENGINE_BUILD}/{b.asset}"],
                }
                for (system, arch), b in BUILTIN_ENGINES.items()
            },
        },
    }
