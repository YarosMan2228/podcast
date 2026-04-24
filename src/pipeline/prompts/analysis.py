"""Episode-analysis prompt + response schema.

SPEC §4.4 defines the exact task; this module is the single source of truth
for the prompt wording and the response schema it must conform to. Changing
the schema here is a contract break — all downstream artifact workers read
``Analysis.*_json`` fields assuming this shape.

Design notes:

- The transcript goes in a cached ``system`` block so downstream text-artifact
  calls within the 5-minute TTL read it at 10% cost
  (``.claude/rules/claude-api-usage.md §4``).
- We ship *both* full_text and a compact ``[id=N start=Xms end=Yms] text``
  segment listing: full_text gives Claude the sentence flow, the segment
  list lets it emit ``start_ms``/``end_ms`` aligned to real transcript
  boundaries instead of inventing timestamps.
- JSON-only output is enforced in-prompt and validated by pydantic downstream;
  no OpenAI-style "JSON mode" exists for Claude (``claude-api-usage.md §5``).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Response schema — mirrors SPEC §1.5 EpisodeAnalysis
# ---------------------------------------------------------------------------


class Guest(BaseModel):
    name: str
    bio: str


class Chapter(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=120)

    @field_validator("end_ms")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("start_ms")
        if start is not None and v <= start:
            raise ValueError(f"end_ms ({v}) must be > start_ms ({start})")
        return v


class ClipCandidate(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    virality_score: int = Field(ge=1, le=10)
    reason: str = Field(min_length=1)
    hook_text: str = Field(min_length=1, max_length=200)

    @field_validator("end_ms")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("start_ms")
        if start is not None and v <= start:
            raise ValueError(f"end_ms ({v}) must be > start_ms ({start})")
        return v


class NotableQuote(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    speaker: str | None = None
    ts_ms: int = Field(ge=0)


class EpisodeAnalysisSchema(BaseModel):
    """Validated shape of what Claude returns. Matches SPEC §1.5."""

    episode_title: str = Field(min_length=1, max_length=60)
    hook: str = Field(min_length=1, max_length=120)
    guest: Guest | None = None
    themes: list[str] = Field(min_length=1, max_length=6)
    chapters: list[Chapter] = Field(min_length=1, max_length=10)
    clip_candidates: list[ClipCandidate] = Field(min_length=2, max_length=15)
    notable_quotes: list[NotableQuote] = Field(min_length=1, max_length=20)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


SYSTEM_INSTRUCTIONS = """\
You are a podcast analyst. Given a full transcript with word-level timestamps, \
extract structured metadata for cross-platform content repurposing. \
Return ONLY valid JSON matching the provided schema. \
No preamble, no explanation, no markdown fences.\
"""


_SCHEMA_INLINE = """\
{
  "episode_title": "<string, max 60 chars, punchy>",
  "hook": "<string, max 120 chars, one sentence>",
  "guest": null | {"name": "<string>", "bio": "<string, 1 sentence>"},
  "themes": ["<3-5 single-word or 2-word phrases>"],
  "chapters": [
    {"start_ms": <int>, "end_ms": <int>, "title": "<string>"}
  ],
  "clip_candidates": [
    {
      "start_ms": <int>,
      "end_ms": <int>,
      "virality_score": <int 1-10>,
      "reason": "<string>",
      "hook_text": "<string, max 200 chars>"
    }
  ],
  "notable_quotes": [
    {"text": "<string, max 200 chars>", "speaker": "<string|null>", "ts_ms": <int>}
  ]
}\
"""


_TASK = """\
Your task:
1. Generate episode_title (max 60 chars, punchy, not generic).
2. Generate hook — one sentence that makes someone click (< 120 chars).
3. Detect guest if present: name + 1-sentence bio. If no guest, use null.
4. Extract 3–5 themes (single-word or 2-word phrases).
5. Segment into 4–8 chapters with start_ms/end_ms aligned to segment boundaries.
6. Find TOP 10 clip_candidates (30–60 seconds each):
   - High emotional intensity OR surprising claim OR strong storytelling.
   - Self-contained: a listener understands without earlier context.
   - virality_score 1–10 — honest, not inflated.
   - hook_text is the catchphrase that makes the clip shareable.
7. Extract 10–15 notable_quotes — one-liners under 200 chars with ts_ms.

Return JSON matching this exact shape:
"""


def _format_segments_for_prompt(segments: list[dict[str, Any]], max_segments: int = 400) -> str:
    """Render segments as ``[id=N start=Xms end=Yms] text`` lines.

    Truncation: long-form podcasts can produce 500+ segments. We cap at
    ``max_segments`` so the prompt stays within a reasonable token budget;
    downstream stitching still sees the full list because ``full_text`` is
    shipped in the cached block.
    """
    lines: list[str] = []
    sample = segments[:max_segments]
    for seg in sample:
        sid = seg.get("id", 0)
        start = seg.get("start_ms", 0)
        end = seg.get("end_ms", 0)
        text = (seg.get("text") or "").strip().replace("\n", " ")
        lines.append(f"[id={sid} start={start}ms end={end}ms] {text}")
    if len(segments) > max_segments:
        lines.append(f"... [{len(segments) - max_segments} additional segments truncated]")
    return "\n".join(lines)


def build_messages(
    full_text: str,
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(system_blocks, messages)`` ready for ``ClaudeClient.call``.

    The transcript block is cached (ephemeral, 5-min TTL) — text-artifact
    workers build their system blocks with the same cached transcript, so
    subsequent calls read at 10% cost.
    """
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS},
        {
            "type": "text",
            "text": (
                "<transcript_full_text>\n"
                f"{full_text}\n"
                "</transcript_full_text>\n\n"
                "<segments_with_timestamps>\n"
                f"{_format_segments_for_prompt(segments)}\n"
                "</segments_with_timestamps>"
            ),
            # §4 prompt caching — the transcript is re-used by every
            # downstream text-artifact call within 5 minutes.
            "cache_control": {"type": "ephemeral"},
        },
    ]

    user_content = _TASK + _SCHEMA_INLINE + "\n\nReturn ONLY the JSON object."
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    return system_blocks, messages


def retry_user_message(previous_response: str, error: str) -> list[dict[str, Any]]:
    """Corrective user message for a validation-failed retry (§6).

    The assistant's prior (invalid) text is echoed back so Claude sees what
    it produced and what specifically to fix.
    """
    return [
        {"role": "assistant", "content": previous_response},
        {
            "role": "user",
            "content": (
                f"That response failed validation: {error}. "
                "Return ONLY valid JSON matching the schema — no preamble, "
                "no markdown fences, no commentary."
            ),
        },
    ]
