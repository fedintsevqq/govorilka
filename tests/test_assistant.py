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
    helper = assistant.CallAssistant(lambda: assistant.ChatEndpoint(url, "m"), "Я тимлид",
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
