import numpy as np

from govorilka.segmenter import Segmenter, SegmenterParams

SR = 16_000


def tone(seconds: float, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def feed_in_chunks(seg: Segmenter, audio: np.ndarray, chunk_s: float = 0.1):
    events = []
    step = int(SR * chunk_s)
    for i in range(0, len(audio), step):
        events.extend(seg.feed(audio[i : i + step], i / SR))
    return events


def test_silence_produces_no_events():
    events = feed_in_chunks(Segmenter(), silence(3))
    assert events == []


def test_phrase_then_pause_gives_one_final_with_start_time():
    audio = np.concatenate([silence(1.0), tone(1.5), silence(1.0)])
    events = feed_in_chunks(Segmenter(), audio)
    finals = [e for e in events if e.kind == "final"]
    assert len(finals) == 1
    # старт с учётом preroll 0.3 с
    assert 0.6 <= finals[0].start <= 1.0
    assert len(finals[0].audio) / SR >= 1.5


def test_partials_emitted_during_long_phrase():
    audio = np.concatenate([tone(3.0), silence(1.0)])
    events = feed_in_chunks(Segmenter(), audio)
    partials = [e for e in events if e.kind == "partial"]
    assert len(partials) >= 2
    assert events[-1].kind == "final"


def test_short_click_is_discarded():
    audio = np.concatenate([silence(0.5), tone(0.06), silence(1.0)])
    events = feed_in_chunks(Segmenter(), audio)
    assert [e.kind for e in events] == ["discard"]


def test_long_speech_is_split_at_max_length():
    params = SegmenterParams(max_segment_s=2.0, partial_every_s=100)
    audio = np.concatenate([tone(5.0), silence(1.0)])
    events = feed_in_chunks(Segmenter(params), audio)
    finals = [e for e in events if e.kind == "final"]
    assert len(finals) == 3
    assert all(len(e.audio) / SR <= 2.05 for e in finals)
    assert finals[1].start > finals[0].start


def test_flush_closes_open_phrase():
    seg = Segmenter()
    feed_in_chunks(seg, tone(1.0))
    events = seg.flush()
    assert [e.kind for e in events] == ["final"]
    assert seg.flush() == []
