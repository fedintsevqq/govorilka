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


def test_autopaste_waits_for_the_chat_window_and_pastes_once():
    from govorilka.qt.autopaste import paste_when_ready

    now = [0.0]
    titles = iter(["Проводник"] * 4 + ["Новая вкладка — Google Chrome"] * 4 + ["ChatGPT — Google Chrome"] * 1000)
    pasted = []
    ok = paste_when_ready(
        "chatgpt", title=lambda: next(titles), paste=lambda: pasted.append(now[0]),
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert ok == 1 and len(pasted) == 1
    assert pasted[0] >= 2.0 + 2.5  # вставили, когда окно чата уже постояло впереди


def test_autopaste_never_pastes_into_another_window():
    from govorilka.qt.autopaste import paste_when_ready

    now = [0.0]
    # чат мелькнул на секунду, потом человек ушёл в мессенджер — туда вставлять нельзя
    titles = iter(["Gemini - Google Chrome"] * 4 + ["Telegram"] * 1000)
    pasted = []
    ok = paste_when_ready(
        "gemini", title=lambda: next(titles), paste=lambda: pasted.append(1),
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert not ok and pasted == []


def test_autopaste_waits_out_the_login_page_and_pastes_after_it():
    from govorilka.qt.autopaste import paste_when_ready

    now = [0.0]
    titles = iter(["Вход – Google Аккаунты — Google Chrome"] * 30 + ["Gemini - Google Chrome"] * 20 + ["Telegram"] * 2000)
    pasted = []
    count = paste_when_ready(
        "gemini", title=lambda: next(titles), paste=lambda: pasted.append(now[0]),
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert count == 1 and pasted[0] > 30 * 0.4  # не на странице входа, а уже в чате


def test_autopaste_pastes_again_when_the_person_logged_in_after_the_first_paste():
    from govorilka.qt.autopaste import paste_when_ready

    now = [0.0]
    # без входа ChatGPT открылся сразу с полем ввода → вставили; человек пошёл входить → вернулся в чат
    titles = iter(["ChatGPT"] * 10 + ["Log in - OpenAI"] * 20 + ["ChatGPT"] * 20 + ["Telegram"] * 2000)
    pasted = []
    count = paste_when_ready(
        "chatgpt", title=lambda: next(titles), paste=lambda: pasted.append(now[0]),
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert count == 2 and len(pasted) == 2


def test_autopaste_does_not_paste_twice_when_you_just_switch_tabs():
    from govorilka.qt.autopaste import paste_when_ready

    now = [0.0]
    titles = iter(["DeepSeek"] * 10 + ["Почта — Google Chrome"] * 10 + ["DeepSeek"] * 2000)
    pasted = []
    count = paste_when_ready(
        "deepseek", title=lambda: next(titles), paste=lambda: pasted.append(1),
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert count == 1


def test_every_web_chat_is_recognized_by_its_window_title():
    from govorilka.qt.autopaste import TITLES, matches

    assert {key for key, target in ai.TARGETS.items() if target.url} == set(TITLES)
    assert matches("DeepSeek - Into the Unknown - Google Chrome", "deepseek")
    assert matches("Алиса — Яндекс", "alice") and not matches("ChatGPT", "gemini")


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
    assert result.ok and result.url == "https://chatgpt.com/" and result.target == "chatgpt"
    assert "Начинаем." in result.clipboard
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
    c._reachable = lambda url: True
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


def test_gemini_blocked_by_the_network_is_not_opened_even_if_chatgpt_works(tmp_path):
    c, _ = make_controller(tmp_path, "gemini", "SE")
    c._reachable = lambda url: "gemini" not in url
    result = c.send_to_ai()
    assert not result.ok and not result.url and "Gemini не открывается" in result.message


def test_autopaste_waits_while_there_is_no_chat_input_on_the_page():
    from govorilka.qt.autopaste import NoInputError, paste_when_ready

    now = [0.0]
    calls = []

    def paste():
        calls.append(now[0])
        if len(calls) < 3:  # страница ещё с ошибкой — поля ввода нет
            raise NoInputError("нет поля")

    count = paste_when_ready(
        "gemini", title=lambda: "Gemini", paste=paste,
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert count == 1 and len(calls) == 3
