"""ASS subtitle generator with word-level karaoke highlight (SPEC §5.4).

Consumed by the video-clip worker: given the word-level timestamps of the
full transcript and the clip's [start_ms, end_ms] window, emit a valid
Advanced SubStation Alpha (.ass) document that ffmpeg's ``subtitles`` filter
can burn into the 9:16 clip.

Karaoke model (SPEC §5.4 + ``.claude/rules/ffmpeg-usage.md``):

* Each word in the clip window is emitted inside a Dialogue event with a
  ``{\\kN}`` tag — N is the word's duration in **centiseconds**.
* ASS's ``\\k`` semantics: a word stays in ``SecondaryColour`` for N cs,
  then flips to ``PrimaryColour``. We therefore set Primary=yellow (sung)
  and Secondary=white (unsung), giving the classic podcast karaoke look.
* Words are grouped into phrases of up to ``MAX_WORDS_PER_PHRASE`` words;
  a gap larger than ``PAUSE_BREAK_MS`` between consecutive words forces a
  new phrase so the burn-in doesn't run a sentence across a silence.

The file is a pure string builder. Writing it to disk and cleaning up the
temp path is the worker's responsibility (SPEC §5.4 / ffmpeg-usage.md §5).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

# Per SPEC §5.4 — tuneable but these are the values we ship to the demo.
MAX_WORDS_PER_PHRASE: int = 5
PAUSE_BREAK_MS: int = 300

# Style values straight from SPEC §5.4. Colours are in ASS BGR order
# (``&HAABBGGRR`` — alpha first, then B, G, R). Yellow = 00FFFF in BGR.
STYLE_FONTNAME: str = "Inter"
STYLE_FONTSIZE: int = 72
STYLE_PRIMARY: str = "&H0000FFFF"    # yellow — "already sung"
STYLE_SECONDARY: str = "&H00FFFFFF"  # white — "not yet sung"
STYLE_OUTLINE_COLOUR: str = "&H00000000"  # black
STYLE_OUTLINE: int = 4
STYLE_ALIGNMENT: int = 2  # bottom-center
STYLE_MARGIN_V: int = 200


@dataclass(frozen=True)
class Word:
    """A single transcript word with absolute-episode timestamps in ms.

    Matches the ``words[]`` entries in SPEC §1.4's Transcript schema —
    ``w`` / ``start_ms`` / ``end_ms``. We keep a thin dataclass instead of
    passing raw dicts so the grouper can be unit-tested without touching
    the transcript JSON shape.
    """

    text: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        # Whisper occasionally emits end_ms < start_ms for one-letter tokens;
        # clamp to 0 so ``\k`` never gets a negative duration.
        return max(0, self.end_ms - self.start_ms)


# ---------------------------------------------------------------------------
# Parsing + filtering transcript words
# ---------------------------------------------------------------------------


def words_from_segments(segments: Sequence[dict]) -> list[Word]:
    """Flatten ``transcript.segments_json`` into a single ordered word list.

    Silently skips segments without a ``words`` array — those come from
    Whisper responses where word-level timestamps weren't produced (e.g. a
    segment of pure non-speech). Kept permissive so a partially-broken
    transcript still yields what it can.
    """
    out: list[Word] = []
    for seg in segments:
        for w in seg.get("words") or []:
            text = w.get("w") or w.get("text") or ""
            if not text:
                continue
            out.append(
                Word(
                    text=text,
                    start_ms=int(w.get("start_ms", 0)),
                    end_ms=int(w.get("end_ms", 0)),
                )
            )
    return out


def clip_words(
    words: Iterable[Word], clip_start_ms: int, clip_end_ms: int
) -> list[Word]:
    """Keep words whose midpoint falls inside ``[clip_start_ms, clip_end_ms]``.

    The midpoint rule handles edge cases cleanly: a word straddling the
    cut is included iff more than half of it sits inside the clip, which
    avoids a flash of half-a-word at the seam.
    """
    out: list[Word] = []
    for w in words:
        mid = (w.start_ms + w.end_ms) // 2
        if clip_start_ms <= mid < clip_end_ms:
            out.append(w)
    return out


# ---------------------------------------------------------------------------
# Phrase grouping
# ---------------------------------------------------------------------------


def group_into_phrases(
    words: Sequence[Word],
    *,
    max_words: int = MAX_WORDS_PER_PHRASE,
    pause_break_ms: int = PAUSE_BREAK_MS,
) -> list[list[Word]]:
    """Chunk a flat word list into phrases respecting pauses + max length."""
    phrases: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            gap = w.start_ms - current[-1].end_ms
            if gap > pause_break_ms or len(current) >= max_words:
                phrases.append(current)
                current = []
        current.append(w)
    if current:
        phrases.append(current)
    return phrases


# ---------------------------------------------------------------------------
# ASS rendering
# ---------------------------------------------------------------------------


def format_ass_timestamp(ms: int) -> str:
    """Render ``ms`` as ASS ``H:MM:SS.cc`` (centiseconds, single-digit hour)."""
    ms = max(0, int(ms))
    hours = ms // 3_600_000
    ms -= hours * 3_600_000
    minutes = ms // 60_000
    ms -= minutes * 60_000
    seconds = ms // 1000
    cs = (ms - seconds * 1000) // 10
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    """ASS treats ``{`` as an override-block opener and ``\\`` as an escape.

    A raw word like ``{AI}`` or a Unicode em-dash with a backslash would
    break the parser. We strip the hazards rather than doing full ASS-safe
    escaping — burned-in captions don't need exotic typography.
    """
    return (
        text.replace("\\", "")
            .replace("{", "(")
            .replace("}", ")")
    )


def _phrase_dialogue(
    phrase: Sequence[Word],
    clip_start_ms: int,
    style: str = "Default",
    *,
    karaoke: bool = True,
) -> str:
    """Build one ``Dialogue:`` line for a phrase of words.

    Timestamps are **clip-relative** because ffmpeg's subtitles filter
    reads the ASS against the clip's own timeline (the seek happens on
    the input, not inside the subtitle file). ``karaoke=False`` drops the
    ``\\k`` tags so the phrase shows in one colour (Pro "clean" preset).
    """
    phrase_start = max(0, phrase[0].start_ms - clip_start_ms)
    phrase_end = max(phrase_start, phrase[-1].end_ms - clip_start_ms)

    parts: list[str] = []
    for w in phrase:
        if karaoke:
            duration_cs = max(1, w.duration_ms // 10)
            parts.append(f"{{\\k{duration_cs}}}{_escape_ass_text(w.text)}")
        else:
            parts.append(_escape_ass_text(w.text))
    body = " ".join(parts)

    return (
        f"Dialogue: 0,{format_ass_timestamp(phrase_start)},"
        f"{format_ass_timestamp(phrase_end)},{style},,0,0,0,,{body}"
    )


@dataclass(frozen=True)
class CaptionPreset:
    """One burned-in caption look (Pro ``caption_style``).

    ``karaoke`` — SPEC §5.4 default: word-by-word highlight (\\k tags).
    ``clean``   — plain white, thick outline, no per-word highlight.
    ``boxed``   — white text on a translucent black box (BorderStyle 3),
                  word highlight in the brand colour.
    """

    name: str
    fontsize: int
    primary: str
    secondary: str
    outline_colour: str
    back_colour: str
    border_style: int
    outline: int
    shadow: int
    karaoke: bool
    margin_v: int = STYLE_MARGIN_V


def hex_to_ass_colour(hex_rgb: str | None, alpha: int = 0) -> str | None:
    """``#rrggbb`` → ASS ``&HAABBGGRR``; ``None`` for invalid input."""
    if not hex_rgb:
        return None
    h = hex_rgb.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def caption_preset(style: str = "karaoke", highlight_hex: str | None = None) -> CaptionPreset:
    """Resolve a preset by name; unknown names fall back to ``karaoke``.

    ``highlight_hex`` (the job's brand colour) replaces the yellow "sung"
    colour for the karaoke-based presets so captions match the graphics.
    """
    highlight = hex_to_ass_colour(highlight_hex) or STYLE_PRIMARY
    if style == "clean":
        return CaptionPreset(
            name="clean",
            fontsize=68,
            primary="&H00FFFFFF",
            secondary="&H00FFFFFF",
            outline_colour=STYLE_OUTLINE_COLOUR,
            back_colour="&H00000000",
            border_style=1,
            outline=5,
            shadow=1,
            karaoke=False,
        )
    if style == "boxed":
        return CaptionPreset(
            name="boxed",
            fontsize=64,
            primary=highlight,
            secondary="&H00FFFFFF",
            outline_colour="&H80000000",  # box colour (BorderStyle 3 uses OutlineColour)
            back_colour="&H80000000",
            border_style=3,
            outline=14,
            shadow=0,
            karaoke=True,
        )
    return CaptionPreset(
        name="karaoke",
        fontsize=STYLE_FONTSIZE,
        primary=highlight,
        secondary=STYLE_SECONDARY,
        outline_colour=STYLE_OUTLINE_COLOUR,
        back_colour="&H00000000",
        border_style=1,
        outline=STYLE_OUTLINE,
        shadow=0,
        karaoke=True,
    )


def _ass_header(preset: CaptionPreset | None = None) -> str:
    """SPEC §5.4 baseline style (or a Pro preset). One string so diffs read well."""
    p = preset or caption_preset()
    return (
        "[Script Info]\n"
        "Title: Podcast Pack karaoke\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{STYLE_FONTNAME},{p.fontsize},"
        f"{p.primary},{p.secondary},{p.outline_colour},"
        f"{p.back_colour},-1,0,0,0,100,100,0,0,{p.border_style},"
        f"{p.outline},{p.shadow},{STYLE_ALIGNMENT},40,40,{p.margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def build_ass(
    words: Sequence[Word],
    clip_start_ms: int,
    clip_end_ms: int,
    *,
    max_words_per_phrase: int = MAX_WORDS_PER_PHRASE,
    pause_break_ms: int = PAUSE_BREAK_MS,
    style: str = "karaoke",
    highlight_hex: str | None = None,
) -> str:
    """Render a full .ass file for the clip window.

    Words outside the window are dropped; remaining words are grouped
    into phrases and each phrase becomes one ``Dialogue`` line with
    ``\\k``-tagged karaoke highlights (unless the preset disables them).
    The output always contains the header + style section even if no
    words fall in the window (the worker treats that as "no captions" and
    still renders a silent clip).
    """
    preset = caption_preset(style, highlight_hex)
    in_clip = clip_words(words, clip_start_ms, clip_end_ms)
    phrases = group_into_phrases(
        in_clip,
        max_words=max_words_per_phrase,
        pause_break_ms=pause_break_ms,
    )
    events = "\n".join(
        _phrase_dialogue(p, clip_start_ms, karaoke=preset.karaoke) for p in phrases
    )
    return _ass_header(preset) + (events + "\n" if events else "")
