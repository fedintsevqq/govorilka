from datetime import datetime

from govorilka.transcript import AutoSaver, Transcript, Utterance, format_offset, safe_filename


def make() -> Transcript:
    return Transcript(datetime(2026, 10, 23, 14, 5), "Планёрка QA")


def test_utterances_sorted_by_start():
    t = make()
    t.add(Utterance("Собеседник", 5.0, "второе"))
    t.add(Utterance("Я", 1.0, "первое"))
    assert [u.text for u in t.utterances] == ["первое", "второе"]


def test_markdown_contains_title_date_and_lines():
    t = make()
    t.add(Utterance("Я", 65, "Привет"))
    md = t.to_markdown()
    assert md.startswith("# Планёрка QA")
    assert "23.10.2026 14:05" in md
    assert "**[01:05] Я:** Привет" in md


def test_format_offset_with_hours():
    assert format_offset(3725) == "1:02:05"
    assert format_offset(-3) == "00:00"


def test_safe_filename_strips_forbidden_chars():
    assert safe_filename('Релиз 2.1: "баги"/фиксы?') == "Релиз_2.1_багификсы"


def test_default_filename():
    assert make().default_filename() == "2026-10-23_14-05_Планёрка_QA.md"
    assert Transcript(datetime(2026, 1, 2, 3, 4)).default_filename() == "2026-01-02_03-04.md"


def test_autosaver_writes_file(tmp_path):
    t = make()
    saver = AutoSaver(t, tmp_path / "sub")
    t.add(Utterance("Я", 0, "тест"))
    path = saver.save()
    assert path is not None and path.read_text(encoding="utf-8") == t.to_markdown()
