"""Куда отдавать разговор нейросети: просто скопировать, чат в браузере (ChatGPT, Claude, Gemini…) или Claude Code.

Защита от блокировки аккаунта. ChatGPT, Claude, Gemini и Claude Code не работают из России и ещё нескольких стран, а
вход в аккаунт оттуда может кончиться баном. Поэтому перед тем как что-то открыть, Говорилка анонимно (без аккаунта
и без текста) спрашивает у самого сервиса, из какой страны он нас видит (`/cdn-cgi/trace`). Если из такой страны
или сервис недоступен — ничего не открываем и не отправляем, а подсказываем включить VPN или выбрать GigaChat,
Алису, DeepSeek: они работают в России.

Claude Code сам читает файл транскрипта. ChatGPT и Claude принимают текст прямо в адресе (`?q=`): короткий разговор
сразу оказывается в поле ввода. Длинный в адрес не влезает (браузеры и Windows режут длинные ссылки), поэтому
текст всегда ещё и в буфере обмена — тогда остаётся вставить. Остальные чаты текст из адреса не берут.
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    name: str  # как называть в кнопке: «Отправить в ChatGPT»
    short: str  # в круговом меню места мало
    url: str = ""  # веб-чат; пусто — не браузер
    query: str = ""  # параметр адреса, которым чат принимает текст запроса; пусто — не умеет
    probe: str = ""  # где спросить, из какой страны нас видно; пусто — сервис работает и без VPN


CLAUDE_CODE = "claude_code"
CLIPBOARD = "clipboard"
MAX_URL = 8_000  # длиннее ссылку не строим: чаты и Windows обрезают длинные адреса, кириллица — 6 знаков на букву

TARGETS: dict[str, Target] = {
    "chatgpt": Target("ChatGPT", "ChatGPT", "https://chatgpt.com/", "q", "https://chatgpt.com/cdn-cgi/trace"),
    "claude": Target("Claude", "Claude", "https://claude.ai/new", "q", "https://claude.ai/cdn-cgi/trace"),
    CLAUDE_CODE: Target("Claude Code", "Claude Code", probe="https://claude.ai/cdn-cgi/trace"),
    # у Google нет такой страницы; Gemini недоступен там же, где ChatGPT, — спрашиваем у него
    "gemini": Target("Gemini", "Gemini", "https://gemini.google.com/app", probe="https://chatgpt.com/cdn-cgi/trace"),
    "deepseek": Target("DeepSeek", "DeepSeek", "https://chat.deepseek.com/"),
    "gigachat": Target("GigaChat", "GigaChat", "https://giga.chat/"),
    "alice": Target("Алису", "Алиса", "https://alice.yandex.ru/"),
    CLIPBOARD: Target("буфер обмена", "Копировать"),
}

# для настроек: как выбор называется в списке
CHOICES: list[tuple[str, str]] = [
    (CLIPBOARD, "Скопировать — вставлю в любую нейросеть сам"),
    ("chatgpt", "ChatGPT — откроется с разговором в браузере"),
    ("claude", "Claude — откроется с разговором в браузере"),
    (CLAUDE_CODE, "Claude Code — на этом компьютере"),
    ("gemini", "Gemini — откроется в браузере"),
    ("deepseek", "DeepSeek — откроется в браузере"),
    ("gigachat", "GigaChat — откроется в браузере"),
    ("alice", "Алиса (Яндекс) — откроется в браузере"),
]


# где эти сервисы не работают (и где вход грозит блокировкой аккаунта)
BLOCKED_REGIONS = {"RU", "BY", "IR", "KP", "CU", "SY", "CN", "HK", "MO"}
PROBE_TIMEOUT_S = 4.0


def probe_country(url: str, timeout: float = PROBE_TIMEOUT_S) -> str | None:
    """Код страны, как её видит сервис (например, «SE»). None — сервис не ответил (заблокирован или нет сети).
    Запрос анонимный: без cookies, аккаунта и текста разговора."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Govorilka"})
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — адрес из списка выше
            body = response.read(4096).decode("ascii", "replace")
    except OSError as exc:
        log.info("Не достучался до %s: %s", url, exc)
        return None
    for line in body.splitlines():
        if line.startswith("loc="):
            return line[4:].strip().upper() or None
    return None


def availability_problem(target: Target, country: str | None) -> str:
    """Почему сейчас нельзя открывать сервис (пустая строка — можно)."""
    if not target.probe:
        return ""
    if country is None:
        return f"{target.name} сейчас не отвечает"
    if country in BLOCKED_REGIONS:
        return f"{target.name} не работает без VPN в вашей стране, а вход без него может привести к блокировке аккаунта"
    return ""


def availability_hint(country: str | None) -> str:
    """Что делать дальше. Без допущений про страну: человек может быть где угодно, с VPN или без."""
    if country in BLOCKED_REGIONS:
        return "Включите VPN и нажмите ещё раз — или выберите GigaChat, Алису или DeepSeek: им VPN не нужен."
    return "Проверьте интернет (или VPN, если он нужен в вашей стране) и нажмите ещё раз — или выберите другую нейросеть."


def resolve(choice: str) -> str:
    """Неизвестное или устаревшее значение (например, прежнее «auto») — просто скопировать."""
    return choice if choice in TARGETS else CLIPBOARD


def chat_url(target: Target, text: str) -> tuple[str, bool]:
    """Адрес чата и то, удалось ли вложить в него сам разговор (если чат умеет и ссылка не слишком длинная)."""
    if target.query:
        url = f"{target.url}?{target.query}={quote(text, safe='')}"
        if len(url) <= MAX_URL:
            return url, True
    return target.url, False


def chat_text(template: str, markdown: str) -> str:
    """Текст для веб-чата: запрос, а под ним весь разговор. {file} в запросе — это и есть разговор ниже."""
    request = template.replace("{file}", "он ниже").strip()
    return f"{request}\n\n---\n\n{markdown.strip()}\n"
