"""Проверка обновлений: спрашиваем у GitHub последний релиз и сравниваем с версией приложения.

Запрос анонимный (без аккаунта, без данных о вас), как у любого браузера. Нет связи или GitHub недоступен —
это не ошибка работы: приложение просто не узнает про обновление. Автопроверку можно выключить в настройках.
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from govorilka import net, version

log = logging.getLogger(__name__)

REPO = "fedintsevqq/govorilka"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT_S = 8.0
NOTES_LIMIT = 700  # в плашке показываем начало описания, остальное — на странице релиза


class UpdateError(RuntimeError):
    """Не удалось узнать, есть ли обновление (нет связи, GitHub отвечает ошибкой, странный ответ)."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str  # «0.3.3»
    page_url: str  # страница релиза: что нового
    notes: str  # начало описания релиза
    asset_name: str = ""  # файл для этой системы; пусто — для неё в релизе файла нет
    asset_url: str = ""
    size: int = 0
    sha256: str = ""  # контрольная сумма файла, как её публикует GitHub (для проверки скачанного)


def asset_suffix(system: str | None = None, machine: str | None = None) -> str:
    """Какой файл релиза подходит этой системе: имена задаёт сборка (.github/workflows/release.yml)."""
    system = system or sys.platform
    if system == "win32":
        return "windows-x64.exe"
    machine = (machine or platform.machine()).lower()
    return "macos-apple-silicon.dmg" if machine in ("arm64", "aarch64") else "macos-intel.dmg"


def pick_asset(assets: list[dict], suffix: str) -> dict | None:
    return next((a for a in assets if str(a.get("name", "")).endswith(suffix)), None)


def parse_release(data: dict, suffix: str | None = None) -> UpdateInfo | None:
    """Описание релиза из ответа GitHub. None — у релиза нет номера версии (не наш формат)."""
    tag = str(data.get("tag_name", ""))
    if version.parse(tag) is None or data.get("draft") or data.get("prerelease"):
        return None
    number = ".".join(str(n) for n in version.parse(tag) or ())
    asset = pick_asset(data.get("assets") or [], suffix or asset_suffix()) or {}
    digest = str(asset.get("digest", ""))
    notes = str(data.get("body") or "").strip()
    return UpdateInfo(
        version=number,
        page_url=str(data.get("html_url") or f"https://github.com/{REPO}/releases/latest"),
        notes=notes[:NOTES_LIMIT] + ("…" if len(notes) > NOTES_LIMIT else ""),
        asset_name=str(asset.get("name", "")),
        asset_url=str(asset.get("browser_download_url", "")),
        size=int(asset.get("size") or 0),
        sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
    )


def fetch_latest(url: str = API_URL, timeout: float = TIMEOUT_S) -> dict:
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Govorilka/{version.VERSION}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=net.ssl_context()) as response:  # noqa: S310
            return json.loads(response.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub ответил ошибкой {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise UpdateError(f"нет связи с GitHub ({exc})") from exc


def check(
    current: str = version.VERSION,
    fetch: Callable[[], dict] = fetch_latest,
    suffix: str | None = None,
) -> UpdateInfo | None:
    """Есть ли релиз новее current. None — у вас последняя версия. UpdateError — узнать не удалось."""
    info = parse_release(fetch(), suffix)
    if info is None:
        raise UpdateError("в ответе GitHub нет подходящего релиза")
    return info if version.is_newer(info.version, current) else None
