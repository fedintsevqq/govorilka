"""Итоги созвона на локальной модели: память компьютера, загрузка с докачкой, нарезка и сборка итогов."""

import hashlib
import http.server
import threading
from datetime import datetime

import pytest

from govorilka import summary
from govorilka.transcript import Transcript, Utterance


def model(min_gb=8, comfortable_gb=12):
    return summary.LlmModel("m", "Модель", "", "a/b", "m.gguf", 10, "0" * 64, min_gb, comfortable_gb, "Apache 2.0")


# ---------- память ----------


def test_too_little_memory_blocks_the_model_and_a_bit_more_warns_it_will_be_slow():
    verdict, note = summary.fit(model(), 4)
    assert verdict == "no" and "4 ГБ" in note and "не меньше 8 ГБ" in note
    verdict, note = summary.fit(model(), 8)
    assert verdict == "slow" and "медленно" in note
    assert summary.fit(model(), 16) == ("ok", "")
    assert summary.fit(model(), 0) == ("ok", "")  # не узнали объём — не мешаем
    assert summary.fit(model(), 7.7)[0] == "slow"  # «8 ГБ» в системе часто 7,7: не отказываем зря


def test_this_computer_reports_its_memory():
    assert summary.total_ram_gb() > 1


def test_catalog_is_ordered_from_recommended_and_every_model_is_pinned():
    assert summary.CATALOG[0].key == summary.DEFAULT_LLM and summary.CATALOG[0].recommended
    for entry in summary.CATALOG:
        assert len(entry.sha256) == 64 and entry.url.startswith("https://huggingface.co/")
        assert entry.min_ram_gb < entry.comfortable_ram_gb
    assert summary.engine_build() is not None  # на машинах сборки (Windows x64, Mac arm64/x64) движок есть


# ---------- загрузка с докачкой ----------


@pytest.fixture
def flaky_server(monkeypatch):
    """Отдаёт файл, но первый ответ обрывает на середине; второй продолжает по Range."""
    data = bytes(range(256)) * 4000
    state = {"requests": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            header = self.headers.get("Range")
            state["requests"].append(header)
            start = int(header.split("=")[1].split("-")[0]) if header else 0
            body = data[start:]
            self.send_response(206 if header else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if len(state["requests"]) == 1:
                self.wfile.write(body[: len(body) // 2])  # обрыв связи
                self.wfile.flush()
                self.connection.close()
                return
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(summary.installer, "_check_url", lambda url, allowed=None: None)
    monkeypatch.setattr(summary.time, "sleep", lambda s: None)
    yield f"http://127.0.0.1:{httpd.server_port}/m.gguf", data, state
    httpd.shutdown()


def test_broken_download_continues_from_the_same_byte(tmp_path, flaky_server):
    url, data, state = flaky_server
    target = tmp_path / "m.gguf"
    statuses = []
    summary.download_resumable(url, target, len(data), "модель", (), statuses.append)
    assert target.read_bytes() == data
    assert state["requests"][0] is None and state["requests"][1].startswith("bytes=") and state["requests"][1] != "bytes=0-"
    assert not (tmp_path / "m.gguf.part").exists()


def test_tampered_model_is_deleted(tmp_path, flaky_server):
    url, data, _ = flaky_server
    target = tmp_path / "m.gguf"
    with pytest.raises(summary.SummaryError, match="Контрольная сумма"):
        summary._fetch(url, target, len(data), "1" * 64, "модель", (), lambda _s: None, lambda: False)
    assert not target.exists()
    summary._fetch(url, target, len(data), hashlib.sha256(data).hexdigest(), "модель", (), lambda _s: None, lambda: False)
    assert target.exists()


@pytest.fixture
def honest_server(monkeypatch):
    """Отдаёт файл с поддержкой Range; запрос дальше конца файла — 416, как у Hugging Face."""
    data = bytes(range(256)) * 300
    state = {"requests": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            header = self.headers.get("Range")
            state["requests"].append(header)
            start = int(header.split("=")[1].split("-")[0]) if header else 0
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206 if header else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(summary.installer, "_check_url", lambda url, allowed=None: None)
    yield f"http://127.0.0.1:{httpd.server_port}/m.gguf", data, state
    httpd.shutdown()


def test_download_of_exact_size_stops_at_the_last_byte(tmp_path, honest_server):
    url, data, state = honest_server
    target = tmp_path / "m.gguf"
    summary.download_resumable(url, target, len(data), "модель", (), lambda _s: None)
    assert target.read_bytes() == data
    assert len(state["requests"]) == 1  # после последнего байта больше ничего не просим


def test_file_of_another_size_at_the_address_means_this_source_is_gone(tmp_path, honest_server):
    # раньше размер округлялся вверх, и загрузка бесконечно просила «ещё»; теперь другой размер — другой файл,
    # и качаем с запасного адреса
    url, data, _ = honest_server
    with pytest.raises(summary.SourceGone):
        summary.download_resumable(url, tmp_path / "m.gguf", len(data) + 1000, "модель", (), lambda _s: None)


def test_mirror_is_used_when_the_main_address_is_gone(tmp_path, honest_server, monkeypatch):
    url, data, state = honest_server
    gone = url.replace("/m.gguf", "/gone.gguf")
    monkeypatch.setattr(summary, "LLM_DIR", tmp_path)
    model = summary.LlmModel("m", "Модель", "", "", "m.gguf", len(data), hashlib.sha256(data).hexdigest(), 1, 2, "",
                             mirrors=(url,))
    monkeypatch.setattr(summary.LlmModel, "urls", property(lambda self: (gone, url)))

    real_urlopen = summary.urllib.request.urlopen

    def urlopen(request, *args, **kwargs):
        if request.full_url == gone:
            raise summary.urllib.error.HTTPError(gone, 404, "Not Found", {}, None)
        return real_urlopen(request, *args, **kwargs)

    monkeypatch.setattr(summary.urllib.request, "urlopen", urlopen)
    assert summary.ensure_model(model, lambda _s: None).read_bytes() == data


def test_disk_full_is_reported_at_once_and_not_retried(tmp_path, honest_server, monkeypatch):
    url, data, state = honest_server
    monkeypatch.setattr(summary.shutil, "disk_usage", lambda _p: type("U", (), {"free": 10})())
    with pytest.raises(summary.DiskProblem, match="Не хватает места"):
        summary.download_resumable(url, tmp_path / "m.gguf", len(data), "модель", (), lambda _s: None)
    assert state["requests"] == []


def test_unverified_file_never_gets_the_final_name(tmp_path, honest_server):
    url, data, _ = honest_server
    target = tmp_path / "m.gguf"
    with pytest.raises(summary.SummaryError, match="Контрольная сумма"):
        summary.download_resumable(url, target, len(data), "модель", (), lambda _s: None, sha256="0" * 64)
    assert not target.exists() and not (tmp_path / "m.gguf.part").exists()


def test_cancel_stops_the_download_even_while_it_waits_to_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(summary.installer, "_check_url", lambda url, allowed=None: None)
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(summary.SummaryError, match="Отменено"):
        summary.download_resumable("http://127.0.0.1:9/m.gguf", tmp_path / "m.gguf", 10, "модель", (), lambda _s: None, cancelled)


def test_two_callers_download_one_file_once_and_it_is_not_mixed(tmp_path, honest_server):
    # подсказки в звонке перезапускались и качали ту же модель вторым потоком: файл получался вперемешку
    url, data, state = honest_server
    target = tmp_path / "m.gguf"
    digest = hashlib.sha256(data).hexdigest()
    errors = []

    def fetch():
        try:
            summary._fetch(url, target, len(data), digest, "модель", (), lambda _s: None, lambda: False)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=fetch) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == [] and target.read_bytes() == data
    assert len(state["requests"]) == 1  # качал один поток, остальные дождались готового файла


def test_partial_file_bigger_than_the_model_is_downloaded_again(tmp_path, honest_server):
    url, data, _ = honest_server
    target = tmp_path / "m.gguf"
    (tmp_path / "m.gguf.part").write_bytes(b"x" * (len(data) + 500))  # остаток прежней испорченной загрузки
    summary._fetch(url, target, len(data), hashlib.sha256(data).hexdigest(), "модель", (), lambda _s: None, lambda: False)
    assert target.read_bytes() == data


def test_catalog_sizes_are_exact_bytes():
    for entry in summary.CATALOG:
        assert entry.size > 500 * 1024 * 1024 and entry.size % (1024 * 1024) != 0  # байты, а не округлённые мегабайты
        assert entry.size_mb == round(entry.size / (1024 * 1024))


# ---------- итоги ----------


class FakeServer:
    def __init__(self):
        self.prompts = []

    def chat(self, system, user, max_tokens=summary.ANSWER_TOKENS):
        self.prompts.append(user)
        return "<think>рассуждаю</think>\n\n### Кратко\nОбсудили релиз."


def test_short_conversation_is_summarised_in_one_go_and_thinking_is_removed():
    server = FakeServer()
    text = summary.summarize(server, "[00:01] Я: Привет")
    assert text == "### Кратко\nОбсудили релиз." and len(server.prompts) == 1
    assert "### Задачи" in server.prompts[0]


def test_long_conversation_is_read_in_parts_and_then_summarised():
    server = FakeServer()
    long = "\n".join(f"[00:{i:02d}] Я: " + "слово " * 400 for i in range(60))
    statuses = []
    summary.summarize(server, long, statuses.append)
    parts = summary.split_transcript(long)
    assert len(parts) > 1 and len(server.prompts) == len(parts) + 1
    assert "часть 1 из" in statuses[0] and statuses[-1] == "Составляю итоги…"
    assert all(len(p) <= summary.CHUNK_CHARS + 3000 for p in parts)  # реплика целиком, кусок не раздувается


def test_summary_goes_on_top_of_the_markdown_file():
    transcript = Transcript(datetime(2026, 10, 25, 14, 0), "Планёрка")
    transcript.add(Utterance("Я", 1, "Привет", 2, "mic"))
    transcript.summary = "### Кратко\nОбсудили релиз."
    text = transcript.to_markdown()
    assert text.index("## Итоги") < text.index("### Кратко") < text.index("## Разговор") < text.index("Привет")


def test_controller_refuses_a_model_this_computer_cannot_run(tmp_path, monkeypatch):
    from govorilka.config import Settings
    from govorilka.controller import Controller

    class Listener:
        def __getattr__(self, name):
            return lambda *a, **k: None

    class NoHotkeys:
        def set_chord(self, chord): ...

    c = Controller(Listener(), Settings(transcripts_dir=str(tmp_path), summary_model="yandexgpt-5-lite-8b"), hotkeys=NoHotkeys())
    with pytest.raises(summary.SummaryError, match="не о чем"):
        c.make_summary(lambda _s: None)
    c.transcript = Transcript(datetime(2026, 10, 25, 14, 0))
    c.transcript.add(Utterance("Я", 1, "Привет", 2, "mic"))
    monkeypatch.setattr(summary, "total_ram_gb", lambda: 8)
    with pytest.raises(summary.SummaryError, match="не меньше 12 ГБ"):
        c.make_summary(lambda _s: None)


def test_controller_saves_the_summary_into_the_transcript_file(tmp_path, monkeypatch):
    from govorilka.config import Settings
    from govorilka.controller import Controller

    class Listener:
        def __getattr__(self, name):
            return lambda *a, **k: None

    class NoHotkeys:
        def set_chord(self, chord): ...

    class Server(FakeServer):
        def __init__(self, engine, weights):
            super().__init__()
            self.stopped = False

        def wait_ready(self, cancelled=None):
            pass

        def stop(self):
            Server.stopped = True

    monkeypatch.setattr(summary, "ensure_engine", lambda *a: tmp_path / "llama-server")
    monkeypatch.setattr(summary, "ensure_model", lambda *a: tmp_path / "m.gguf")
    monkeypatch.setattr(summary, "LlamaServer", Server)
    monkeypatch.setattr(summary, "total_ram_gb", lambda: 32)
    c = Controller(Listener(), Settings(transcripts_dir=str(tmp_path)), hotkeys=NoHotkeys())
    c.transcript = Transcript(datetime(2026, 10, 25, 14, 0), "Планёрка")
    c.transcript.add(Utterance("Я", 1, "Привет", 2, "mic"))
    c.last_file = tmp_path / "созвон.md"
    text = c.make_summary(lambda _s: None)
    assert "Обсудили релиз" in text and Server.stopped
    assert "## Итоги" in c.last_file.read_text(encoding="utf-8")


def test_empty_sections_and_thinking_are_removed_from_the_answer():
    raw = "<think>думаю</think>\n### Кратко\nОбсудили релиз.\n\n### Задачи\n- Я — тесты\n\n### Открытые вопросы\nНет\n"
    assert summary.clean_answer(raw) == "### Кратко\nОбсудили релиз.\n\n### Задачи\n- Я — тесты"
    assert summary.clean_answer("### Решения\n—\n### Задачи\n- Я — тесты") == "### Задачи\n- Я — тесты"
    assert "Нет" in summary.clean_answer("### Решения\nНет, так не пойдёт: нужно позже.")  # настоящий текст не трогаем


def test_model_text_is_shown_without_images_links_and_html():
    shown = summary.safe_markdown("См. ![x](https://evil/a.png?d=1) и [сайт](https://evil.com) <img src=x> "
                                  "https://evil.com/p **жирно**, 3 < 5 > 2")
    assert "evil/a.png" not in shown and "](" not in shown and "<img" not in shown and "https://" not in shown
    assert "**жирно**" in shown and "сайт" in shown and "3 < 5 > 2" in shown


def test_garbage_from_the_model_is_recognised():
    assert summary.looks_like_text("Сегодня план: доделать релиз и проверить оплату.")
    assert summary.looks_like_text("Plan for today: ship the release.")
    garbage = "limp一经$）\r\n cruzأوضاعstants dictHSChooserVi之力 <![/hooks）"
    assert not summary.looks_like_text(garbage)
    assert not summary.looks_like_text("   ")


def test_server_with_broken_gpu_math_is_restarted_on_the_processor(monkeypatch, tmp_path):
    started = []

    class Server:
        def __init__(self, engine, model, threads=None, gpu=True):
            self.metal, self.stopped = gpu, False
            started.append(self)

        def wait_ready(self, cancelled=None):
            pass

        def chat(self, system, user, max_tokens=0):
            return "一经$）cruzأوضاع之力" if self.metal else "План: релиз в пятницу."

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(summary, "LlamaServer", Server)
    server = summary.start_server(tmp_path, tmp_path)
    assert [s.metal for s in started] == [True, False] and started[0].stopped and server is started[1]
