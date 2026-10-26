"""Каталог моделей итогов по сети и защита сетевых запросов: адреса, переадресации, установка движка."""

import copy
import hashlib
import http.server
import io
import json
import tarfile
import threading

import pytest

from govorilka import model_catalog, net, summary


@pytest.fixture(autouse=True)
def builtin_catalog(tmp_path, monkeypatch):
    """Каждый тест начинает со встроенного каталога и своей папки данных."""
    monkeypatch.setattr(summary, "LLM_DIR", tmp_path)
    yield
    model_catalog.apply({"format": model_catalog.FORMAT, "llm": []})


def document():
    return copy.deepcopy(model_catalog.builtin_document())


def test_builtin_document_is_a_valid_catalog_and_matches_the_repository_file():
    model_catalog.parse(document())
    from pathlib import Path

    stored = json.loads((Path(__file__).parent.parent / "catalog" / "models.json").read_text(encoding="utf-8"))
    assert stored == document()  # catalog/models.json собирается из встроенного: tools/check_models.py --write


def test_new_address_from_the_network_replaces_the_dead_one_and_old_stays_as_fallback():
    data = document()
    data["llm"][1]["urls"] = ["https://modelscope.cn/models/new/place/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf"]
    model_catalog.apply(data)
    model = summary.CATALOG_BY_KEY["qwen2.5-1.5b"]
    assert model.urls[0].startswith("https://modelscope.cn/models/new/")
    assert any("huggingface.co" in url for url in model.urls[1:])  # встроенный адрес — запасной


def test_a_new_model_can_be_added_from_the_network():
    data = document()
    entry = dict(data["llm"][1], key="new-model", title="Новая", file="new.gguf")
    data["llm"].append(entry)
    model_catalog.apply(data)
    assert summary.CATALOG[-1].key == "new-model" and summary.CATALOG_BY_KEY["new-model"].title == "Новая"


@pytest.mark.parametrize("breakage", [
    lambda d: d["llm"][0].update(sha256="nope"),
    lambda d: d["llm"][0].update(urls=["http://huggingface.co/x.gguf"]),
    lambda d: d["llm"][0].update(urls=["https://evil.example/x.gguf"]),
    lambda d: d["llm"][0].update(urls=["https://evilhuggingface.co/x.gguf"]),
    lambda d: d["llm"][0].update(file="../../outside.gguf"),
    lambda d: d["llm"][0].update(size="big"),
    lambda d: d["llm"].append(dict(d["llm"][0])),
    lambda d: d.update(format=99),
    lambda d: d["engine"]["platforms"]["win32-x64"].update(binary="../../evil.exe"),
])
def test_a_broken_catalog_is_rejected_whole(breakage):
    data = document()
    breakage(data)
    with pytest.raises(model_catalog.CatalogError):
        model_catalog.parse(data)


def test_refresh_without_network_keeps_the_catalog(monkeypatch):
    before = list(summary.CATALOG)

    def offline(*_args, **_kwargs):
        raise OSError("нет сети")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", offline)
    assert model_catalog.refresh() is False
    assert summary.CATALOG == before


def test_cached_catalog_is_used_at_start_and_a_broken_one_is_ignored(tmp_path):
    data = document()
    data["llm"][0]["title"] = "Из кеша"
    model_catalog.cache_path().write_text(json.dumps(data), encoding="utf-8")
    model_catalog.load_cached()
    assert summary.CATALOG[0].title == "Из кеша"
    model_catalog.cache_path().write_text("{испорчен", encoding="utf-8")
    model_catalog.load_cached()  # не падает


# ---------- сеть ----------


@pytest.mark.parametrize("url,local", [
    ("http://127.0.0.1:8080/v1", True),
    ("http://localhost:1234/v1", True),
    ("http://[::1]:8080/v1", True),
    ("http://127.0.0.1.attacker.example/v1", False),
    ("http://127.0.0.1@attacker.example/v1", False),
    ("https://api.openai.com/v1", False),
])
def test_only_real_loopback_addresses_count_as_this_computer(url, local):
    assert net.is_loopback(url) is local


def test_api_key_is_not_sent_along_a_redirect():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/stolen")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        from govorilka import assistant

        endpoint = assistant.ChatEndpoint(f"http://127.0.0.1:{httpd.server_port}/v1", "m", "secret")
        with pytest.raises(assistant.AssistantError):
            assistant.stream_chat(endpoint, [{"role": "user", "content": "привет"}], lambda _t: None)
    finally:
        httpd.shutdown()
    assert [path for path, _ in hits] == ["/v1/chat/completions"]  # на /stolen не пошли


# ---------- движок ----------


def _engine_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        payload = b"#!/bin/sh\necho llama\n"
        info = tarfile.TarInfo(f"llama-{summary.ENGINE_BUILD}/llama-server")
        info.size, info.mode = len(payload), 0o755
        bundle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_engine_is_installed_once_by_two_callers_and_marked_complete(monkeypatch):
    archive = _engine_archive()
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(archive)))
            self.end_headers()
            self.wfile.write(archive)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    build = summary.EngineBuild("engine.tar.gz", hashlib.sha256(archive).hexdigest(),
                                f"llama-{summary.ENGINE_BUILD}/llama-server", (f"http://127.0.0.1:{httpd.server_port}/e.tar.gz",))
    monkeypatch.setattr(summary, "engine_build", lambda: build)
    monkeypatch.setattr(summary.installer, "_check_url", lambda url, allowed=None: None)
    results, errors = [], []

    def install():
        try:
            results.append(summary.ensure_engine(lambda _s: None))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=install) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    httpd.shutdown()
    assert errors == [] and len(set(results)) == 1 and results[0].is_file()
    assert len(requests) == 1  # скачал и распаковал один поток
    assert (summary.engine_dir() / summary.ENGINE_DONE).is_file()
    assert not list(summary.LLM_DIR.glob("engine-*"))  # следов распаковки не осталось


def test_half_installed_engine_without_the_mark_is_not_used(monkeypatch):
    build = summary.EngineBuild("e.tar.gz", "0" * 64, "llama-server")
    monkeypatch.setattr(summary, "engine_build", lambda: build)
    summary.engine_dir().mkdir(parents=True)
    (summary.engine_dir() / "llama-server").write_text("x")
    assert summary.engine_path() is None


def test_deleting_a_model_also_removes_its_unfinished_part(tmp_path):
    model = summary.CATALOG[1]
    model.path.parent.mkdir(parents=True, exist_ok=True)
    model.path.with_name(model.path.name + ".part").write_bytes(b"x" * 2048)
    summary.delete_model(model)
    assert not model.path.with_name(model.path.name + ".part").exists()
