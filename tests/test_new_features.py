import gc
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest

from govorilka.models import format_progress
from govorilka.segmenter import Segmenter, SegmenterParams
from govorilka.speakers import Diarizer
from govorilka.transcript import Utterance
from govorilka.transcript_view import DARK, LIGHT, TranscriptView, common_prefix, detect_palette, is_dark

SR = 16_000


def tone(seconds: float, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# ---------- разрез длинной фразы ----------


def test_long_phrase_is_cut_at_quiet_dip_not_at_hard_limit():
    params = SegmenterParams(max_segment_s=5.0, cut_search_s=2.0, partial_every_s=100)
    # 4 с речи, провал громкости 0.1 с (но ещё «речь» для детектора), потом снова речь
    audio = np.concatenate([tone(4.0), tone(0.1, amp=0.03), tone(3.0), np.zeros(SR, dtype=np.float32)])
    events = [e for e in _feed(Segmenter(params), audio) if e.kind == "final"]
    first_len = len(events[0].audio) / SR
    assert 4.0 <= first_len <= 4.15  # разрез в провале, а не на 5-й секунде
    assert abs(events[1].start - first_len) < 0.05


def _feed(seg, audio):
    out = []
    for i in range(0, len(audio), 1600):
        out.extend(seg.feed(audio[i : i + 1600], i / SR))
    return out


# ---------- голоса ----------


def test_diarizer_groups_similar_voices_and_opens_new_ones():
    a, b = np.zeros(4, dtype=np.float32), np.zeros(4, dtype=np.float32)
    a[0], b[1] = 1, 1
    voices = iter([a, a + 0.1, b, a])
    diarizer = Diarizer(embed=lambda _: next(voices))
    long = np.zeros(SR * 2, dtype=np.float32)
    assert [diarizer.assign(long) for _ in range(4)] == [1, 1, 2, 1]


def test_short_phrase_keeps_previous_speaker_without_embedding():
    diarizer = Diarizer(embed=lambda _: pytest.fail("короткую фразу не надо сравнивать"))
    diarizer.last = 3
    assert diarizer.assign(np.zeros(SR // 2, dtype=np.float32)) == 3


# ---------- окно ----------


def test_common_prefix_compares_objects():
    u1, u2, u3 = Utterance("Я", 0, "a"), Utterance("Я", 1, "b"), Utterance("Я", 1, "b")
    assert common_prefix([u1, u2], [u1, u2, u3]) == 2
    assert common_prefix([u1, u2], [u1, u3]) == 1  # равный по тексту, но другой объект


@pytest.fixture
def view():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("нет дисплея")
    root.withdraw()
    yield TranscriptView(root)
    root.destroy()
    gc.collect()  # см. test_hotkeys.root: переменные Tk удаляем в главном потоке


def _lines(view):
    return [line for line in view.text.get("1.0", "end").splitlines() if line]


def test_view_appends_inserts_out_of_order_and_shows_partials(view):
    u1, u2, u3 = Utterance("Я", 0, "раз"), Utterance("Собеседник 1", 5, "три"), Utterance("Я", 2, "два")
    view.show([u1, u2], [])
    partial = SimpleNamespace(start=9, speaker="Я", text="черно", source="mic")
    view.show([u1, u2], [partial])
    assert _lines(view)[-1].endswith("черно …")
    view.show([u1, u3, u2], [])  # реплика пришла не по порядку
    assert [line.split(": ", 1)[1] for line in _lines(view)] == ["раз", "два", "три"]
    view.show([u1, u2], [])  # фильтр эха удалил реплику
    assert len(_lines(view)) == 2


def test_view_gives_speakers_distinct_colors(view):
    assert view.color_of("Я") != view.color_of("Собеседник 1")
    view.show([Utterance("Собеседник 1", 0, "а"), Utterance("Собеседник 2", 1, "б")], [])
    assert view.color_of("Собеседник 1") != view.color_of("Собеседник 2")


# ---------- прогресс скачивания ----------


def test_download_progress_text():
    mb = 1024 * 1024
    assert format_progress("модель", 425 * mb, 850 * mb) == "Скачиваю модель: 425 из 850 МБ (50%)"
    assert format_progress("модель", 12 * mb, None) == "Скачиваю модель: 12 МБ…"


# ---------- тёмная тема ----------


def test_is_dark_by_luminance():
    assert is_dark(0x1e * 257, 0x1f * 257, 0x22 * 257)
    assert not is_dark(65535, 65535, 65535)


def test_palettes_have_readable_contrast():
    def luminance(color: str) -> float:
        r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for palette in (LIGHT, DARK):
        for foreground in (palette.text, palette.me, *palette.others):
            assert abs(luminance(foreground) - luminance(palette.background)) > 0.25, (palette.background, foreground)


def test_view_sets_explicit_text_color(view):
    assert view.text.cget("fg") in (view.palette.text, view.palette.text.upper())
    assert detect_palette(view.text.master) in (LIGHT, DARK)


def test_partials_get_rarer_on_long_phrases():
    """Черновик распознаёт фразу целиком, поэтому на длинной фразе его частота падает: работы в 2 с лишним раза меньше."""

    def partial_seconds(params):
        events = _feed(Segmenter(params), np.concatenate([tone(20.0), np.zeros(SR, dtype=np.float32)]))
        return sum(len(e.audio) for e in events if e.kind == "partial") / SR

    before = partial_seconds(SegmenterParams(partial_growth=0.0))  # как было: черновик каждую секунду
    after = partial_seconds(SegmenterParams())
    print(f"черновики на 20 с речи: было {before:.0f} с звука, стало {after:.0f} с")
    assert after < before / 2
    events = _feed(Segmenter(), tone(3.0))
    assert sum(e.kind == "partial" for e in events) >= 2  # короткие фразы по-прежнему обновляются каждую секунду
