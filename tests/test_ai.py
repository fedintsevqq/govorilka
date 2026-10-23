import json
from datetime import datetime

from govorilka import ai
from govorilka.config import DEFAULT_PROMPT, OLD_DEFAULT_PROMPT, load_settings
from govorilka.transcript import Transcript, Utterance


def test_auto_picks_claude_code_only_when_it_is_installed():
    assert ai.resolve("auto", claude_installed=True) == ai.CLAUDE_CODE
    assert ai.resolve("auto", claude_installed=False) == "chatgpt"
    assert ai.resolve("gemini", claude_installed=True) == "gemini"
    assert ai.resolve("что-то старое", claude_installed=True) == "chatgpt"


def test_every_choice_in_settings_is_a_known_target():
    assert {key for key, _ in ai.CHOICES} == set(ai.TARGETS) | {ai.AUTO}


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
    assert not c.send_to_ai().ok  # пока нечего отправлять
    c.transcript = Transcript(datetime(2026, 1, 1), "Планёрка")
    c.transcript.add(Utterance("Я", 1.0, "Начинаем.", 2.0, "mic", "Я"))
    result = c.send_to_ai()
    assert result.ok and result.url == "https://chatgpt.com/" and "Начинаем." in result.clipboard
    c.settings = Settings(transcripts_dir=str(tmp_path), ai_target="clipboard")
    result = c.send_to_ai()
    assert result.ok and not result.url and "Начинаем." in result.clipboard
