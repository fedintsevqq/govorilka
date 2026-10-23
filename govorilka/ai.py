"""Куда отдавать разговор нейросети: Claude Code на этом компьютере или чат в браузере (ChatGPT, Claude, Gemini…).

С Claude Code всё просто: он сам читает файл транскрипта. Веб-чаты файл с диска не видят, поэтому туда уходит текст:
Говорилка кладёт в буфер обмена запрос и весь разговор и открывает чат — остаётся вставить и нажать Enter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    name: str  # как называть в кнопке: «Отправить в ChatGPT»
    short: str  # в круговом меню места мало
    url: str = ""  # веб-чат; пусто — не браузер


CLAUDE_CODE = "claude_code"
CLIPBOARD = "clipboard"
AUTO = "auto"  # Claude Code, если он установлен, иначе ChatGPT

TARGETS: dict[str, Target] = {
    "chatgpt": Target("ChatGPT", "ChatGPT", "https://chatgpt.com/"),
    "claude": Target("Claude", "Claude", "https://claude.ai/new"),
    CLAUDE_CODE: Target("Claude Code", "Claude Code"),
    "gemini": Target("Gemini", "Gemini", "https://gemini.google.com/app"),
    "deepseek": Target("DeepSeek", "DeepSeek", "https://chat.deepseek.com/"),
    "gigachat": Target("GigaChat", "GigaChat", "https://giga.chat/"),
    "alice": Target("Алису", "Алиса", "https://alice.yandex.ru/"),
    CLIPBOARD: Target("буфер обмена", "Копировать"),
}

# для настроек: как выбор называется в списке
CHOICES: list[tuple[str, str]] = [
    (AUTO, "Сама: Claude Code, если установлен, иначе ChatGPT"),
    ("chatgpt", "ChatGPT — откроется в браузере"),
    ("claude", "Claude — откроется в браузере"),
    (CLAUDE_CODE, "Claude Code — на этом компьютере"),
    ("gemini", "Gemini — откроется в браузере"),
    ("deepseek", "DeepSeek — откроется в браузере"),
    ("gigachat", "GigaChat — откроется в браузере"),
    ("alice", "Алиса (Яндекс) — откроется в браузере"),
    (CLIPBOARD, "Только скопировать — вставлю куда нужно сам"),
]


def resolve(choice: str, claude_installed: bool) -> str:
    """Какая нейросеть на самом деле: «сама» превращается в конкретную, неизвестное значение — в ChatGPT."""
    if choice == AUTO:
        return CLAUDE_CODE if claude_installed else "chatgpt"
    return choice if choice in TARGETS else "chatgpt"


def chat_text(template: str, markdown: str) -> str:
    """Текст для веб-чата: запрос, а под ним весь разговор. {file} в запросе — это и есть разговор ниже."""
    request = template.replace("{file}", "он ниже").strip()
    return f"{request}\n\n---\n\n{markdown.strip()}\n"
