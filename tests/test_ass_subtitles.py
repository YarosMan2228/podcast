"""pipeline.ass_subtitles — ASS karaoke generator (SPEC §5.4).

Pure-string builder; no IO, no Django, no ffmpeg. Tests focus on:

* transcript → word flattening,
* clip-window filtering by word midpoint,
* phrase grouping by ``max_words`` and pause gaps,
* timestamp formatting,
* end-to-end ASS output shape.
"""
from __future__ import annotations

from pipeline.ass_subtitles import (
    MAX_WORDS_PER_PHRASE,
    PAUSE_BREAK_MS,
    Word,
    build_ass,
    clip_words,
    format_ass_timestamp,
    group_into_phrases,
    words_from_segments,
)


# -------------------- Word / duration --------------------


def test_word_duration_clamps_negative_to_zero() -> None:
    """Whisper occasionally emits end_ms < start_ms for tiny tokens — we
    don't want that to become a negative \\k duration downstream."""
    assert Word("ah", 1000, 900).duration_ms == 0


def test_word_duration_positive() -> None:
    assert Word("hello", 1000, 1480).duration_ms == 480


# -------------------- words_from_segments --------------------


def test_words_from_segments_flattens_and_preserves_order() -> None:
    segs = [
        {
            "start_ms": 0,
            "end_ms": 2000,
            "words": [
                {"w": "hello", "start_ms": 0, "end_ms": 500},
                {"w": "world", "start_ms": 500, "end_ms": 1000},
            ],
        },
        {
            "start_ms": 2000,
            "end_ms": 4000,
            "words": [{"w": "bye", "start_ms": 2500, "end_ms": 2800}],
        },
    ]
    words = words_from_segments(segs)
    assert [w.text for w in words] == ["hello", "world", "bye"]
    assert words[2].start_ms == 2500


def test_words_from_segments_skips_segments_without_word_timestamps() -> None:
    segs = [
        {"start_ms": 0, "end_ms": 1000, "text": "untimed", "words": None},
        {"start_ms": 0, "end_ms": 1000, "text": "still none"},
        {"start_ms": 0, "end_ms": 1000, "words": [{"w": "yes", "start_ms": 0, "end_ms": 500}]},
    ]
    assert [w.text for w in words_from_segments(segs)] == ["yes"]


def test_words_from_segments_skips_blank_tokens() -> None:
    segs = [{"words": [{"w": "", "start_ms": 0, "end_ms": 0}, {"w": "ok", "start_ms": 0, "end_ms": 100}]}]
    assert [w.text for w in words_from_segments(segs)] == ["ok"]


# -------------------- clip_words (midpoint filter) --------------------


def test_clip_words_includes_words_whose_midpoint_sits_inside_window() -> None:
    words = [
        Word("a", 0, 200),       # mid=100 → out of [1000, 2000]
        Word("b", 1100, 1400),   # mid=1250 → in
        Word("c", 1900, 2200),   # mid=2050 → out
        Word("d", 1800, 2100),   # mid=1950 → in
    ]
    kept = clip_words(words, 1000, 2000)
    assert [w.text for w in kept] == ["b", "d"]


def test_clip_words_window_is_half_open() -> None:
    """A word with midpoint exactly at clip_end_ms is excluded (end is
    exclusive) — otherwise the next clip would double-count it."""
    words = [Word("edge", 900, 1100)]  # mid = 1000
    assert clip_words(words, 500, 1000) == []
    assert clip_words(words, 500, 1001) == words


# -------------------- group_into_phrases --------------------


def test_group_into_phrases_splits_at_pause_gap() -> None:
    words = [
        Word("one", 0, 200),
        Word("two", 200, 400),
        # 500ms silence > PAUSE_BREAK_MS (300ms) → phrase break
        Word("three", 900, 1100),
    ]
    phrases = group_into_phrases(words, max_words=5, pause_break_ms=300)
    assert [[w.text for w in p] for p in phrases] == [["one", "two"], ["three"]]


def test_group_into_phrases_respects_max_words() -> None:
    # Zero gap, so only the max-words rule can split.
    words = [Word(f"w{i}", i * 100, i * 100 + 100) for i in range(7)]
    phrases = group_into_phrases(words, max_words=3, pause_break_ms=10_000)
    assert [len(p) for p in phrases] == [3, 3, 1]


def test_group_into_phrases_empty_input_returns_empty() -> None:
    assert group_into_phrases([]) == []


# -------------------- format_ass_timestamp --------------------


def test_format_ass_timestamp_centiseconds_and_clamp() -> None:
    # 1h 2m 3s 450ms = 3_723_450ms; ms%1000 = 450 → 45 cs
    assert format_ass_timestamp(3_723_450) == "1:02:03.45"
    # Negative input gets clamped to zero — callers shouldn't pass it,
    # but we don't want a "-0:00:-1.00" nonsense string leaking out.
    assert format_ass_timestamp(-500) == "0:00:00.00"


# -------------------- build_ass (end-to-end) --------------------


def _sample_words() -> list[Word]:
    # 5 words at 500ms each, 100ms pause between each (no phrase break).
    return [
        Word("one", 10_000, 10_500),
        Word("two", 10_600, 11_100),
        Word("three", 11_200, 11_700),
        Word("four", 11_800, 12_300),
        Word("five", 12_400, 12_900),
    ]


def test_build_ass_produces_header_and_dialogue() -> None:
    ass = build_ass(_sample_words(), clip_start_ms=10_000, clip_end_ms=13_000)

    # Header sections present.
    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "[Events]" in ass
    # Style line carries SPEC §5.4 values.
    assert "Inter" in ass
    assert "72" in ass
    # One phrase (5 words, no pause break) → exactly one Dialogue event.
    assert ass.count("Dialogue:") == 1
    # \k tags sit before each rendered word.
    for token in ("one", "two", "three", "four", "five"):
        assert f"}}{token}" in ass
    # Five \k tags = one per word.
    assert ass.count("{\\k") == 5


def test_build_ass_timestamps_are_clip_relative() -> None:
    """A clip starting at 10_000ms should emit its first phrase at
    00:00 (clip-relative), not 00:10 — the ffmpeg ``subtitles=`` filter
    operates on the already-cut clip's timeline."""
    ass = build_ass(_sample_words(), clip_start_ms=10_000, clip_end_ms=13_000)
    # First word starts at clip_start → relative 0s = "0:00:00.00".
    assert "0:00:00.00" in ass


def test_build_ass_empty_when_no_words_in_range() -> None:
    words = [Word("outside", 0, 500)]
    ass = build_ass(words, clip_start_ms=10_000, clip_end_ms=20_000)
    # Header still emitted (ffmpeg can parse it as "zero captions"),
    # but no Dialogue line is written.
    assert "[Events]" in ass
    assert "Dialogue:" not in ass


def test_build_ass_splits_phrases_across_long_pause() -> None:
    words = [
        Word("before", 1000, 1400),
        Word("after", 5000, 5400),  # 3600ms gap » PAUSE_BREAK_MS
    ]
    ass = build_ass(words, 0, 10_000)
    # Two phrases → two Dialogue events.
    assert ass.count("Dialogue:") == 2


def test_build_ass_escapes_ass_override_braces() -> None:
    """Raw ``{`` / ``}`` in a word would open a fake override block."""
    words = [Word("{evil}", 0, 500)]
    ass = build_ass(words, 0, 1000)
    # The word survives but without the structural braces.
    assert "(evil)" in ass
    # Still exactly one \k override for that word.
    assert ass.count("{\\k") == 1


# -------------------- module constants (sanity) --------------------


def test_module_constants_match_spec() -> None:
    """SPEC §5.4 ships ``MAX_WORDS_PER_PHRASE=5``, ``PAUSE_BREAK_MS=300``."""
    assert MAX_WORDS_PER_PHRASE == 5
    assert PAUSE_BREAK_MS == 300
