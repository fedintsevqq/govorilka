"""Подсказки в звонке: поиск вопросов, потоковый ответ, свежий вопрос важнее старого."""

import http.server
import json
import threading
import time

import pytest

from govorilka import assistant


@pytest.mark.parametrize("text,expected", [
    ("Как вы оцениваете сроки по релизу?", True),
    ("Расскажите про ваш опыт с Kubernetes.", True),
    ("Почему вы ушли с прошлой работы", True),
    ("Привет всем, начинаем.", False),
    ("Как бы то ни было, переходим дальше.", False),
    ("Что?", False),
])
def test_questions_are_recognised_without_false_alarms(text, expected):
    assert assistant.is_question(text) is expected


@pytest.fixture
def sse_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            Handler.last = body
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ["Срок ", "— пятница."]:
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1", Handler
    httpd.shutdown()


def test_answer_streams_piece_by_piece(sse_server):
    url, handler = sse_server
    seen = []
    text = assistant.stream_chat(assistant.ChatEndpoint(url, "m"), [{"role": "user", "content": "?"}], seen.append)
    assert text == "Срок — пятница." and seen == ["Срок ", "Срок — пятница."]
    assert handler.last["stream"] is True and handler.last["model"] == "m"


def test_api_key_is_never_sent_over_plain_http():
    with pytest.raises(assistant.AssistantError, match="https"):
        assistant.stream_chat(assistant.ChatEndpoint("http://example.com/v1", "m", "secret"), [], lambda _t: None)


def test_assistant_answers_questions_from_the_other_side_only(sse_server):
    url, _ = sse_server
    events = []
    helper = assistant.CallAssistant(lambda _cancelled: assistant.ChatEndpoint(url, "m"), "Я тимлид",
                                     lambda kind, text: events.append((kind, text)))
    helper.start()
    helper.feed("Я", "Как дела у команды?", from_other=False)  # мой вопрос — не подсказываем
    helper.feed("Собеседник 1", "Когда релиз?", from_other=True)
    end = time.monotonic() + 5
    while time.monotonic() < end and not any(k == "done" for k, _ in events):
        time.sleep(0.02)
    helper.stop()
    assert ("question", "Когда релиз?") in events and ("done", "Срок — пятница.") in events
    assert not any(k == "question" and t == "Как дела у команды?" for k, t in events)


def test_profile_and_context_reach_the_model():
    messages = assistant.build_messages("Когда релиз?", ["Собеседник 1: Привет"], "Я тимлид")
    assert "Я тимлид" in messages[1]["content"] and "Собеседник 1: Привет" in messages[1]["content"]


# ---------- подключение к записи и настройкам ----------


class Listener:
    def __init__(self):
        self.texts, self.assistant = [], []

    def __getattr__(self, name):
        return lambda *a, **k: None

    def on_text(self, event):
        self.texts.append(event)

    def on_assistant(self, kind, text):
        self.assistant.append((kind, text))


class NoHotkeys:
    def set_chord(self, chord): ...


@pytest.fixture
def controller(tmp_path, monkeypatch):
    from govorilka import controller as controller_module
    from govorilka.config import Settings

    monkeypatch.setattr(controller_module, "save_settings", lambda s: True)
    listener = Listener()
    c = controller_module.Controller(listener, Settings(transcripts_dir=str(tmp_path)), hotkeys=NoHotkeys())
    return c, listener, controller_module


def test_finished_phrases_reach_the_assistant_with_who_said_them(controller):
    from govorilka.pipeline import TextEvent

    c, listener, _ = controller
    fed = []

    class Helper:
        def feed(self, speaker, text, from_other):
            fed.append((speaker, text, from_other))

    c._assistant = Helper()
    c._on_session_text(TextEvent("system", "Собеседник 1", "partial", "Когда рел", 0, 1))  # черновик не нужен
    c._on_session_text(TextEvent("system", "Собеседник 1", "final", "Когда релиз?", 0, 1))
    c._on_session_text(TextEvent("mic", "Я", "final", "В пятницу.", 1, 2))
    assert fed == [("Собеседник 1", "Когда релиз?", True), ("Я", "В пятницу.", False)]
    assert len(listener.texts) == 3  # лента получает всё, как раньше


def test_api_endpoint_needs_a_key_and_an_address(controller, monkeypatch):
    from dataclasses import replace

    c, _, module = controller
    monkeypatch.setattr(module.secrets_store, "get_secret", lambda name: "")
    with pytest.raises(assistant.AssistantError, match="ключ"):
        c.assistant_endpoint()
    monkeypatch.setattr(module.secrets_store, "get_secret", lambda name: "sk-test")
    endpoint = c.assistant_endpoint()
    assert endpoint.base_url == "https://api.openai.com/v1" and endpoint.model == "gpt-4o-mini" and endpoint.api_key == "sk-test"
    c.settings = replace(c.settings, assistant_api_preset="custom", assistant_api_url="")
    with pytest.raises(assistant.AssistantError, match="адрес"):
        c.assistant_endpoint()
    c.settings = replace(c.settings, assistant_api_url="https://llm.example/v1", assistant_api_model="my-model")
    assert c.assistant_endpoint() == assistant.ChatEndpoint("https://llm.example/v1", "my-model", "sk-test")


def test_switching_hints_on_during_a_call_starts_them_and_off_stops_them(controller, monkeypatch):
    from dataclasses import replace

    c, listener, module = controller
    started, stopped = [], []
    monkeypatch.setattr(c, "_start_assistant", lambda: started.append(1))

    class Helper:
        def stop(self):
            stopped.append(1)

    c.session = object()
    c.state = module.AppState.RECORDING
    c.update_settings(replace(c.settings, assistant_enabled=True))
    assert started == [1]
    c._assistant = Helper()
    c.update_settings(replace(c.settings, assistant_enabled=False))
    assert stopped == [1] and ("off", "") in listener.assistant and started == [1]


def test_stopping_the_recording_stops_the_hints(controller):
    c, listener, module = controller
    stopped = []

    class Helper:
        def stop(self):
            stopped.append(1)

    class Session:
        def stop(self):
            return None

        transcript = None

    c._assistant = Helper()
    c.session = Session()
    c.state = module.AppState.RECORDING
    c._after_recording = lambda session: None
    c.stop_recording()
    assert stopped == [1] and ("off", "") in listener.assistant


def test_capture_guard_reports_failure_instead_of_crashing():
    from govorilka.qt import capture_guard

    assert capture_guard.protect(0) == ""  # нет такого окна: «не получилось», без исключения
