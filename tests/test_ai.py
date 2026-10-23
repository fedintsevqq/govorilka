import json
from datetime import datetime

from govorilka import ai
from govorilka.config import DEFAULT_PROMPT, OLD_DEFAULT_PROMPT, load_settings
from govorilka.transcript import Transcript, Utterance


def test_default_is_copy_and_unknown_choices_fall_back_to_it():
    from govorilka.config import Settings

    assert Settings().ai_target == ai.CLIPBOARD
    assert ai.resolve("gemini") == "gemini"
    assert ai.resolve("auto") == ai.CLIPBOARD  # прежнее значение по умолчанию
    assert {key for key, _ in ai.CHOICES} == set(ai.TARGETS) and ai.CHOICES[0][0] == ai.CLIPBOARD


def test_short_conversation_goes_inside_the_chat_link_long_one_does_not():
    url, inside = ai.chat_url(ai.TARGETS["chatgpt"], "Привет & пока?")
    assert inside and url.startswith("https://chatgpt.com/?q=") and "&" not in url.split("?q=")[1]
    url, inside = ai.chat_url(ai.TARGETS["claude"], "слово " * 5000)
    assert not inside and url == "https://claude.ai/new"
    url, inside = ai.chat_url(ai.TARGETS["gemini"], "коротко")
    assert not inside and url == "https://gemini.google.com/app"


def test_chat_text_puts_the_request_above_the_whole_conversation():
    text = ai.chat_text("Транскрипт созвона: {file}. Сделай резюме.", "# Созвон\n\n**Я:** привет")
    assert text.startswith("Транскрипт созвона: он ниже. Сделай резюме.")
    assert text.rstrip().endswith("**Я:** привет") and "{file}" not in text


def test_untouched_old_prompt_is_upgraded_but_a_custom_one_is_kept(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"claude_prompt": OLD_DEFAULT_PROMPT}), encoding="utf-8")
    assert load_settings(path).claude_prompt == DEFAULT_PROMPT
    path.write_text(json.dumps({"claude_prompt": "Своё: {file}"}), encoding="utf-8")
    assert load_settings(path).claude_prompt == "Своё: {file}"


def test_web_chat_gets_the_text_on_the_clipboard_and_its_address(tmp_path):
    from govorilka.config import Settings
    from govorilka.controller import Controller, Listener

    class NoHotkeys:
        def set_chord(self, chord): ...
        def start(self): ...
        def stop(self): ...

    c = Controller(Listener(), Settings(transcripts_dir=str(tmp_path), ai_target="chatgpt"), hotkeys=NoHotkeys())
    c._probe_country = lambda url: "DE"  # без сети: тест не должен зависеть от того, где его запускают
    assert not c.send_to_ai().ok  # пока нечего отправлять
    c.transcript = Transcript(datetime(2026, 1, 1), "Планёрка")
    c.transcript.add(Utterance("Я", 1.0, "Начинаем.", 2.0, "mic", "Я"))
    result = c.send_to_ai()
    assert result.ok and result.url.startswith("https://chatgpt.com/?q=") and "Начинаем." in result.clipboard
    c.settings = Settings(transcripts_dir=str(tmp_path), ai_target="clipboard")
    result = c.send_to_ai()
    assert result.ok and not result.url and "Начинаем." in result.clipboard


def make_controller(tmp_path, target, country):
    from govorilka.config import Settings
    from govorilka.controller import Controller, Listener

    class NoHotkeys:
        def set_chord(self, chord): ...
        def start(self): ...
        def stop(self): ...

    c = Controller(Listener(), Settings(transcripts_dir=str(tmp_path), ai_target=target), hotkeys=NoHotkeys())
    probed = []
    c._probe_country = lambda url: probed.append(url) or country
    c.transcript = Transcript(datetime(2026, 1, 1), "Планёрка")
    c.transcript.add(Utterance("Я", 1.0, "Начинаем.", 2.0, "mic", "Я"))
    return c, probed


def test_foreign_chat_is_not_opened_from_a_blocked_country(tmp_path):
    for target in ("chatgpt", "claude", "gemini", "claude_code"):
        c, probed = make_controller(tmp_path, target, "RU")
        result = c.send_to_ai()
        assert not result.ok and not result.url and probed  # ничего не открыли и не отправили
        assert "Начинаем." in result.clipboard and "VPN" in result.message and "GigaChat" in result.message


def test_unreachable_chat_is_not_opened_and_the_hint_does_not_assume_russia(tmp_path):
    c, _ = make_controller(tmp_path, "chatgpt", None)
    result = c.send_to_ai()
    assert not result.ok and not result.url and "в России" not in result.message


def test_chat_opens_normally_anywhere_else_without_vpn(tmp_path):
    for country in ("DE", "US", "KZ", "SE"):
        c, _ = make_controller(tmp_path, "chatgpt", country)
        result = c.send_to_ai()
        assert result.ok and result.url.startswith("https://chatgpt.com/")


def test_services_that_work_in_russia_open_without_any_check(tmp_path):
    for target in ("gigachat", "alice", "deepseek", "clipboard"):
        c, probed = make_controller(tmp_path, target, "RU")
        assert c.send_to_ai().ok and probed == []


def test_country_probe_reads_the_cloudflare_trace(monkeypatch):
    import io

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: Answer(b"fl=1\nip=1.2.3.4\nloc=se\nwarp=off\n"))
    assert ai.probe_country("https://chatgpt.com/cdn-cgi/trace") == "SE"

    def offline(*a, **k):
        raise OSError("нет сети")

    monkeypatch.setattr(ai.urllib.request, "urlopen", offline)
    assert ai.probe_country("https://chatgpt.com/cdn-cgi/trace") is None
