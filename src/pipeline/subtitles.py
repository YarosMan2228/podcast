"""Transcript export formats — SRT, WebVTT and a readable plain-text version.

Pure string builders over ``Transcript.segments_json`` (SPEC §1.4 shape).
Used by ``workers.transcript_worker`` to produce the TRANSCRIPT artifact:
YouTube / LinkedIn / Spotify all accept SRT or VTT for captions, and the
plain text is what people paste into show notes or a blog.

Cue policy: one cue per Whisper segment, but a segment longer than
``MAX_CUE_CHARS`` is split on word boundaries using the word-level
timestamps, so a 40-second run-on sentence doesn't become one unreadable
wall of text on screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

MAX_CUE_CHARS = 84
PARAGRAPH_GAP_MS = 2_000
MAX_PARAGRAPH_CHARS = 600


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _fmt(ms: int, sep: str) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def format_srt_time(ms: int) -> str:
    """``HH:MM:SS,mmm`` — SRT uses a comma before milliseconds."""
    return _fmt(ms, ",")


def format_vtt_time(ms: int) -> str:
    """``HH:MM:SS.mmm`` — WebVTT uses a dot."""
    return _fmt(ms, ".")


def format_clock(ms: int) -> str:
    """``MM:SS`` or ``H:MM:SS`` for the plain-text transcript margins."""
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds = rem // 1000
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _split_long_segment(seg: dict[str, Any], max_chars: int) -> list[Cue]:
    words = seg.get("words") or []
    text = (seg.get("text") or "").strip()
    if len(text) <= max_chars or not words:
        return [Cue(int(seg.get("start_ms", 0)), int(seg.get("end_ms", 0)), text)]

    cues: list[Cue] = []
    chunk: list[dict[str, Any]] = []
    chunk_len = 0
    for w in words:
        token = (w.get("w") or w.get("text") or "").strip()
        if not token:
            continue
        if chunk and chunk_len + 1 + len(token) > max_chars:
            cues.append(
                Cue(
                    int(chunk[0].get("start_ms", 0)),
                    int(chunk[-1].get("end_ms", 0)),
                    " ".join((c.get("w") or c.get("text") or "").strip() for c in chunk),
                )
            )
            chunk, chunk_len = [], 0
        chunk.append(w)
        chunk_len += len(token) + (1 if chunk_len else 0)
    if chunk:
        cues.append(
            Cue(
                int(chunk[0].get("start_ms", 0)),
                int(chunk[-1].get("end_ms", 0)),
                " ".join((c.get("w") or c.get("text") or "").strip() for c in chunk),
            )
        )
    return cues


def build_cues(
    segments: Sequence[dict[str, Any]], *, max_chars: int = MAX_CUE_CHARS
) -> list[Cue]:
    """Flatten segments into display cues; drops empty text, keeps order."""
    cues: list[Cue] = []
    for seg in segments:
        if not (seg.get("text") or "").strip():
            continue
        for cue in _split_long_segment(seg, max_chars):
            if cue.text:
                # Guarantee end > start so players don't reject the cue.
                end = cue.end_ms if cue.end_ms > cue.start_ms else cue.start_ms + 500
                cues.append(Cue(cue.start_ms, end, cue.text))
    return cues


def build_srt(segments: Sequence[dict[str, Any]]) -> str:
    """SubRip: ``N\\nstart --> end\\ntext\\n\\n`` blocks, 1-indexed."""
    blocks: list[str] = []
    for i, cue in enumerate(build_cues(segments), start=1):
        blocks.append(
            f"{i}\n{format_srt_time(cue.start_ms)} --> {format_srt_time(cue.end_ms)}\n{cue.text}\n"
        )
    return "\n".join(blocks) + ("\n" if blocks else "")


def build_vtt(segments: Sequence[dict[str, Any]]) -> str:
    """WebVTT: header + cues; no numbering needed."""
    blocks = ["WEBVTT", ""]
    for cue in build_cues(segments):
        blocks.append(
            f"{format_vtt_time(cue.start_ms)} --> {format_vtt_time(cue.end_ms)}\n{cue.text}\n"
        )
    return "\n".join(blocks) + ("\n" if len(blocks) > 2 else "")


def build_plain_transcript(
    segments: Sequence[dict[str, Any]],
    *,
    paragraph_gap_ms: int = PARAGRAPH_GAP_MS,
    max_paragraph_chars: int = MAX_PARAGRAPH_CHARS,
) -> str:
    """Readable transcript: ``[MM:SS] paragraph`` per speech run.

    Consecutive segments are merged into a paragraph until there is a
    pause longer than ``paragraph_gap_ms`` or the paragraph grows past
    ``max_paragraph_chars`` — a rough but effective stand-in for speaker
    turns when diarization is off.
    """
    paragraphs: list[tuple[int, str]] = []
    current_start: int | None = None
    current_end = 0
    current_parts: list[str] = []

    def _flush() -> None:
        if current_parts and current_start is not None:
            paragraphs.append((current_start, " ".join(current_parts)))

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = int(seg.get("start_ms", 0))
        end = int(seg.get("end_ms", start))
        gap = start - current_end if current_parts else 0
        too_long = sum(len(p) for p in current_parts) + len(text) > max_paragraph_chars
        if current_parts and (gap > paragraph_gap_ms or too_long):
            _flush()
            current_parts = []
            current_start = None
        if current_start is None:
            current_start = start
        current_parts.append(text)
        current_end = end
    _flush()

    return "\n\n".join(f"[{format_clock(s)}] {t}" for s, t in paragraphs) + (
        "\n" if paragraphs else ""
    )
