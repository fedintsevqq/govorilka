import pytest
import json
from datetime import datetime

from govorilka import ai
from govorilka.config import DEFAULT_PROMPT, OLD_DEFAULT_PROMPT, load_settings
from govorilka.transcript import Transcript, Utterance


@pytest.fixture(autouse=True)
def same_poll_everywhere(monkeypatch):
    """На Mac опрос окна реже (раз в секунду): тесты автовставки считают время в шагах Windows."""
    from govorilka.qt import autopaste

    monkeypatch.setattr(autopaste, "POLL_S", 0.4)


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


# ---------- настольные приложения ----------


def fake_apps(monkeypatch, installed):
    from govorilka import desktop_apps

    opened, launched = [], []
    monkeypatch.setattr(desktop_apps, "installed", lambda key: key in installed)
    monkeypatch.setattr(desktop_apps, "open_link", opened.append)
    monkeypatch.setattr(desktop_apps, "launch", launched.append)
    return opened, launched


def test_installed_claude_app_gets_a_short_conversation_right_in_the_input(tmp_path, monkeypatch):
    opened, _ = fake_apps(monkeypatch, {"claude"})
    c, _ = make_controller(tmp_path, "claude", "DE")
    result = c.send_to_ai()
    assert result.ok and not result.url and not result.target  # браузер не нужен, вставлять самим не надо
    assert opened[0].startswith("claude://claude.ai/new?q=") and "%D0%9D%D0%B0%D1%87%D0%B8%D0%BD%D0%B0%D0%B5%D0%BC" in opened[0]


def test_long_conversation_goes_to_the_claude_app_through_autopaste(tmp_path, monkeypatch):
    opened, _ = fake_apps(monkeypatch, {"claude"})
    c, _ = make_controller(tmp_path, "claude", "DE")
    for i in range(60):
        c.transcript.add(Utterance("Я", 10.0 + i, "Длинная фраза про релиз и тестирование.", 11.0 + i, "mic", "Я"))
    result = c.send_to_ai()
    assert opened == ["claude://claude.ai/new"] and result.target == "claude" and not result.url
    assert "Длинная фраза" in result.clipboard


def test_installed_chatgpt_app_is_launched_and_filled_by_autopaste(tmp_path, monkeypatch):
    opened, launched = fake_apps(monkeypatch, {"chatgpt"})
    c, _ = make_controller(tmp_path, "chatgpt", "DE")
    result = c.send_to_ai()
    assert launched == ["chatgpt"] and opened == [] and result.target == "chatgpt" and not result.url


def test_browser_is_used_when_the_app_is_missing_or_the_person_prefers_it(tmp_path, monkeypatch):
    opened, launched = fake_apps(monkeypatch, set())
    c, _ = make_controller(tmp_path, "chatgpt", "DE")
    assert c.send_to_ai().url == "https://chatgpt.com/" and not launched
    opened, launched = fake_apps(monkeypatch, {"chatgpt", "claude"})
    c, _ = make_controller(tmp_path, "chatgpt", "DE")
    c.settings = replace_settings(c.settings, ai_open_in="browser")
    assert c.send_to_ai().url == "https://chatgpt.com/" and not launched and not opened


def replace_settings(settings, **changes):
    from dataclasses import replace

    return replace(settings, **changes)


def test_claude_code_opens_in_the_claude_app_with_the_transcripts_folder(tmp_path, monkeypatch):
    opened, _ = fake_apps(monkeypatch, {"claude"})
    c, _ = make_controller(tmp_path, "claude_code", "DE")
    c.last_file = tmp_path / "call.md"
    c.last_file.write_text("# Созвон", encoding="utf-8")
    result = c.send_to_ai()
    assert result.ok and opened[0].startswith("claude://code/new?q=") and "&folder=" in opened[0]


def test_app_that_fails_to_open_falls_back_to_the_browser(tmp_path, monkeypatch):
    from govorilka import desktop_apps

    fake_apps(monkeypatch, {"chatgpt"})
    monkeypatch.setattr(desktop_apps, "launch", lambda key: (_ for _ in ()).throw(OSError("нет")))
    c, _ = make_controller(tmp_path, "chatgpt", "DE")
    assert c.send_to_ai().url == "https://chatgpt.com/"


def test_foreign_app_is_not_opened_from_a_blocked_country_either(tmp_path, monkeypatch):
    opened, launched = fake_apps(monkeypatch, {"claude", "chatgpt"})
    for target in ("claude", "chatgpt", "claude_code"):
        c, _ = make_controller(tmp_path, target, "RU")
        assert not c.send_to_ai().ok
    assert opened == [] and launched == []


def test_windows_search_checks_every_drive_not_only_c(monkeypatch):
    from govorilka import desktop_apps as d

    monkeypatch.setattr(d.os.path, "exists", lambda p: p == "F:\Program Files\ChatGPT\ChatGPT.exe")
    assert d._windows_exe_on_any_drive(d.APPS["chatgpt"]) == "F:\Program Files\ChatGPT\ChatGPT.exe"
    monkeypatch.setattr(d.os.path, "exists", lambda p: False)
    assert d._windows_exe_on_any_drive(d.APPS["chatgpt"]) is None


def test_mac_search_finds_an_app_on_an_external_disk_through_spotlight(monkeypatch):
    from types import SimpleNamespace

    from govorilka import desktop_apps as d

    app = "/Volumes/Работа/Apps/ChatGPT.app"
    monkeypatch.setattr(d.Path, "exists", lambda self: str(self).replace("\\", "/") == app)
    monkeypatch.setattr(d.subprocess, "run", lambda cmd, **k: SimpleNamespace(stdout=app + "\n", returncode=0))
    assert d._mac_path(d.APPS["chatgpt"]).replace("\\", "/") == app


def test_missing_app_is_rechecked_after_a_few_minutes_found_one_is_remembered(monkeypatch):
    from govorilka import desktop_apps as d

    d.forget()
    clock, calls = [1000.0], []
    monkeypatch.setattr(d.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(d.sys, "platform", "darwin")
    answers = iter([None, "/Applications/Claude.app", "/Applications/Claude.app"])
    monkeypatch.setattr(d, "_mac_path", lambda app: calls.append(1) or next(answers))
    assert d.find("claude") is None and d.find("claude") is None and len(calls) == 1  # «нет» помним, не ищем заново
    clock[0] += d.MISSING_TTL_S + 1  # человек успел установить приложение
    assert d.find("claude") == "/Applications/Claude.app" and len(calls) == 2
    assert d.find("claude") == "/Applications/Claude.app" and len(calls) == 2
    d.forget()


def test_region_check_uses_the_system_certificates(monkeypatch):
    """В собранном приложении на Mac у Python нет системных сертификатов: без certifi проверка всегда «не достучалась»."""
    seen = {}

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return b"loc=DE\n"

    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: seen.update(k) or Answer())
    assert ai.probe_country("https://chatgpt.com/cdn-cgi/trace") == "DE"
    assert seen["context"] is not None  # передан набор системных сертификатов (truststore)
    seen.clear()
    assert ai.reachable("https://gemini.google.com/") is True and seen["context"] is not None


def test_mac_apple_events_denial_is_reported_as_missing_permission(monkeypatch):
    from types import SimpleNamespace

    from govorilka.qt import autopaste

    stderr = "execution error: Not authorized to send Apple events to System Events. (-1743)"
    monkeypatch.setattr(autopaste.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=stderr))
    with pytest.raises(autopaste.NoPermissionError):
        autopaste._osascript("tell application \"System Events\" to get name")
