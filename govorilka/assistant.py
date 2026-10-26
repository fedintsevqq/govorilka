"""Подсказки в звонке: нейросеть слушает вопросы собеседника и сразу предлагает ответ.

Отдельная функция, выключена по умолчанию. Включается в настройках, раздел «Подсказки в звонке».

Откуда ответ:
- локальная модель (llama.cpp, та же, что пишет итоги): разговор не покидает компьютер;
- API по вашему ключу: OpenAI, DeepSeek, OpenRouter, Claude или свой адрес, совместимый с OpenAI. Быстрее и умнее,
  но вопрос и последние реплики уходят провайдеру.

И llama-server, и эти провайдеры понимают один и тот же запрос /v1/chat/completions, поэтому клиент один.
Ответ приходит по кусочкам (stream): первые слова видны через секунду-две, не дожидаясь конца.

Вопрос узнаём по знаку «?» (GigaAM ставит пунктуацию) и по вопросительным словам в начале фразы. Если вопрос не
распознался, кнопка «Ответить» в окне подсказок отвечает на последнюю фразу собеседника.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from govorilka import net

log = logging.getLogger(__name__)

CONTEXT_LINES = 40  # сколько последних реплик видит модель: хватает на тему разговора и не тормозит
ANSWER_TOKENS = 260
TIMEOUT_S = 120
SECRET_NAME = "assistant-api-key"

QUESTION_WORDS = (
    "как", "какой", "какая", "какое", "какие", "каким", "какую", "почему", "зачем", "когда", "где", "куда", "откуда",
    "сколько", "что", "чем", "кто", "кому", "кого", "чей", "чья", "можете", "можешь", "могли", "расскажите",
    "расскажи", "объясните", "объясни", "опишите", "назовите", "приведите", "есть ли", "был ли", "была ли",
    "правда ли", "а если", "а вы", "а ты", "вы когда-нибудь", "давайте обсудим",
)
REQUEST_WORDS = ("расскажите", "расскажи", "объясните", "объясни", "опишите", "назовите", "приведите", "поделитесь",
                 "можете рассказать", "можете объяснить")
_WORD = re.compile(r"[а-яёa-z\-]+", re.IGNORECASE)


def is_question(text: str) -> bool:
    """Похоже ли на вопрос: знак «?» или вопросительное слово в начале фразы из трёх и больше слов."""
    clean = text.strip()
    if len(clean) < 6:
        return False
    if clean.rstrip("»\"').").endswith("?"):
        return True
    words = [w.lower() for w in _WORD.findall(clean)]
    if len(words) < 3:
        return False
    head = " ".join(words[:3])
    # Модель ставит «?» сама. Фраза с точкой — вопрос, только если это просьба («расскажите…»): «как бы то ни было,
    # переходим дальше.» вопросом не считаем. Без знака в конце (модель без пунктуации) — по вопросительному слову.
    words_to_check = REQUEST_WORDS if clean.rstrip("»\"')").endswith((".", "!")) else QUESTION_WORDS
    return any(head == w or head.startswith(w + " ") for w in words_to_check)


# ---------- провайдеры ----------


@dataclass(frozen=True)
class ApiPreset:
    key: str
    title: str
    base_url: str
    model: str  # модель по умолчанию; можно поменять в настройках
    hint: str


API_PRESETS = [
    ApiPreset("openai", "OpenAI (ChatGPT)", "https://api.openai.com/v1", "gpt-4o-mini", "Ключ: platform.openai.com → API keys"),
    ApiPreset("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat", "Ключ: platform.deepseek.com → API keys"),
    ApiPreset("openrouter", "OpenRouter (сотни моделей)", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini",
              "Ключ: openrouter.ai → Keys. Модель — любая из их каталога"),
    ApiPreset("anthropic", "Claude (Anthropic)", "https://api.anthropic.com/v1", "claude-haiku-5-5",
              "Ключ: console.anthropic.com → API keys. Через совместимый с OpenAI адрес"),
    ApiPreset("custom", "Свой адрес", "", "", "Любой сервис, совместимый с OpenAI: адрес вида https://…/v1"),
]
PRESETS_BY_KEY = {preset.key: preset for preset in API_PRESETS}


class AssistantError(RuntimeError):
    """Ответ не получился: текст показываем человеку."""


@dataclass(frozen=True)
class ChatEndpoint:
    base_url: str  # …/v1
    model: str
    api_key: str = ""

    @property
    def is_local(self) -> bool:
        return self.base_url.startswith("http://127.0.0.1")


def stream_chat(
    endpoint: ChatEndpoint,
    messages: list[dict[str, str]],
    on_delta: Callable[[str], None],
    cancelled: Callable[[], bool] = lambda: False,
    max_tokens: int = ANSWER_TOKENS,
) -> str:
    """Запрос /chat/completions с stream=true. Каждый кусочек текста — в on_delta. Возвращает весь ответ."""
    if not endpoint.is_local and not endpoint.base_url.startswith("https://"):
        raise AssistantError("Адрес API должен начинаться с https://: ключ нельзя отправлять открытым текстом")
    body = {"messages": messages, "stream": True, "temperature": 0.3, "max_tokens": max_tokens}
    if endpoint.model:
        body["model"] = endpoint.model
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "Govorilka"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
        headers["x-api-key"] = endpoint.api_key  # Anthropic в режиме совместимости принимает и так
    request = urllib.request.Request(endpoint.base_url.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"), headers=headers)
    parts: list[str] = []
    try:
        context = None if endpoint.is_local else net.ssl_context()
        with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=context) as response:  # noqa: S310
            for raw in response:
                if cancelled():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = json.loads(data)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                delta = (choice.get("delta") or {}).get("content") or ""
                if delta:
                    parts.append(delta)
                    on_delta("".join(parts))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        if exc.code in (401, 403):
            raise AssistantError("Ключ API не подошёл: проверьте его в настройках") from exc
        if exc.code == 404:
            raise AssistantError(f"Модель или адрес не найдены: {endpoint.model or endpoint.base_url}") from exc
        if exc.code == 429:
            raise AssistantError("Провайдер ограничил запросы или закончились деньги на счёте") from exc
        raise AssistantError(f"Провайдер ответил ошибкой {exc.code}: {detail}") from exc
    except OSError as exc:
        raise AssistantError(f"Нет связи с {'моделью' if endpoint.is_local else 'провайдером'}: {exc}") from exc
    return strip_thinking("".join(parts))


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------- запрос ----------

SYSTEM = (
    "Ты подсказываешь человеку прямо во время созвона. Собеседник задал вопрос, человеку надо ответить сейчас. "
    "Напиши ответ, который он может сказать вслух: от первого лица, по-русски, коротко — 2–4 предложения или до "
    "4 коротких пунктов. Сначала главное. Опирайся на разговор и на сведения о человеке, если они есть. Если не знаешь "
    "фактов, не выдумывай цифры и имена: подскажи, как ответить честно или уточнить вопрос. Без вступлений."
)


def build_messages(question: str, context: list[str], profile: str) -> list[dict[str, str]]:
    about = f"Сведения о человеке и теме:\n{profile.strip()}\n\n" if profile.strip() else ""
    history = "\n".join(context[-CONTEXT_LINES:])
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"{about}Разговор (последние реплики):\n{history}\n\nВопрос собеседника: {question}"},
    ]


# ---------- помощник на время записи ----------


Event = Callable[[str, str], None]  # (вид, текст): ready / question / answer / done / error / status


class CallAssistant:
    """Живёт, пока идёт запись. Принимает готовые фразы, на вопросы собеседника отвечает по одному, свежий важнее."""

    def __init__(self, connect: Callable[[Callable[[], bool]], ChatEndpoint], profile: str, on_event: Event,
                 auto: bool = True, stop_server: Callable[[], None] = lambda: None) -> None:
        self._connect = connect  # получает «остановлены ли подсказки»: долгую загрузку модели можно прервать
        self._profile = profile
        self._on_event = on_event
        self._auto = auto
        self._stop_server = stop_server
        self._endpoint: ChatEndpoint | None = None
        self._context: deque[str] = deque(maxlen=CONTEXT_LINES)
        self._last_other = ""
        self._questions: queue.Queue[str | None] = queue.Queue()
        self._generation = 0  # номер текущего вопроса: новый вопрос обрывает ответ на старый
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._worker = threading.Thread(target=self._run, name="call-assistant", daemon=True)

    def start(self) -> None:
        self._worker.start()

    def feed(self, speaker: str, text: str, from_other: bool) -> None:
        """Готовая фраза из записи. from_other — сказал собеседник (а не вы)."""
        if not text.strip():
            return
        with self._lock:
            self._context.append(f"{speaker}: {text.strip()}")
            if from_other:
                self._last_other = text.strip()
        if from_other and self._auto and is_question(text):
            self._ask(text.strip())

    def answer_last(self) -> bool:
        """Кнопка «Ответить»: ответ на последнюю фразу собеседника, даже если она не похожа на вопрос."""
        with self._lock:
            last = self._last_other
        if not last:
            return False
        self._ask(last)
        return True

    def _ask(self, question: str) -> None:
        with self._lock:
            self._generation += 1
        self._questions.put(question)

    def stop(self) -> None:
        self._closed.set()
        self._questions.put(None)
        with self._lock:
            self._generation += 1  # обрывает текущий ответ

    def _run(self) -> None:
        try:
            self._on_event("status", "Подключаю нейросеть…")
            self._endpoint = self._connect(self._closed.is_set)
            if self._closed.is_set():
                return
            self._on_event("ready", "")
            while not self._closed.is_set():
                question = self._questions.get()
                if question is None:
                    break
                while not self._questions.empty():  # пока отвечали, пришли новые: берём самый свежий
                    newer = self._questions.get_nowait()
                    if newer is None:
                        return
                    question = newer
                self._answer(question)
        except Exception as exc:  # noqa: BLE001 — модель не запустилась, нет ключа: человек должен узнать
            if self._closed.is_set():  # подсказки выключили, пока модель готовилась: это не ошибка
                return
            log.exception("Подсказки не запустились")
            self._on_event("error", str(exc))
        finally:
            self._stop_server()

    def _answer(self, question: str) -> None:
        with self._lock:
            generation = self._generation
            context = list(self._context)
        stale = lambda: self._generation != generation or self._closed.is_set()  # noqa: E731
        self._on_event("question", question)
        try:
            text = stream_chat(self._endpoint, build_messages(question, context, self._profile),  # type: ignore[arg-type]
                               lambda partial: None if stale() else self._on_event("answer", strip_thinking(partial)),
                               stale)
        except AssistantError as exc:
            if not stale():
                self._on_event("error", str(exc))
            return
        if not stale():
            self._on_event("done", text)
