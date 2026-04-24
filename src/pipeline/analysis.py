"""Analysis — one Claude call extracts title/hook/clips/themes/chapters/quotes.

SPEC §4.4 flow:

1. Load the Transcript for the job (must exist — transcribe_job ran first).
2. Build the analysis prompt with the transcript in a cached system block
   (the same cached block is reused by downstream text-artifact workers).
3. Call Claude with ``max_tokens=8000``, ``temperature=0.3`` (SPEC §4.4,
   ``.claude/rules/claude-api-usage.md §2``).
4. Strip any stray markdown fences, parse JSON, validate with pydantic.
5. On validation failure: retry up to 3 times — the corrective message
   echoes the assistant's prior response + the specific error
   (``claude-api-usage.md §6``). Final retry drops temperature to 0.
6. Dedupe clip_candidates that overlap in time (SPEC §4.5: keep the higher
   virality_score).
7. Persist via ``update_or_create`` (idempotent, celery-tasks.md §3).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from pydantic import ValidationError

from jobs.models import Analysis, Job, Transcript
from pipeline.prompts.analysis import (
    EpisodeAnalysisSchema,
    build_messages,
    retry_user_message,
)
from services.claude_client import ClaudeError, claude_client

logger = logging.getLogger(__name__)


# Per SPEC §4.4 + .claude/rules/claude-api-usage.md §2-§3.
MAX_TOKENS = 8000
TEMPERATURE = 0.3
FINAL_RETRY_TEMPERATURE = 0.0
MAX_VALIDATION_ATTEMPTS = 3


class AnalysisError(Exception):
    """SPEC §4.5 pipeline-level failure code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------
# Response parsing + clip dedupe
# ---------------------------------------------------------------------------


def _strip_markdown_fences(text: str) -> str:
    """Some models wrap JSON in ```json ... ``` despite being told not to.

    Safer to be tolerant here than bounce the response through another
    retry round-trip.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    # Drop opening fence (possibly ``` or ```json) + closing fence.
    first_newline = text.find("\n")
    if first_newline == -1:
        return text
    body = text[first_newline + 1 :]
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


def _dedupe_overlapping_clips(
    clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """SPEC §4.5: if two clip_candidates overlap in time, keep the higher score.

    We sort by virality_score desc and greedily accept non-overlapping clips.
    Order is preserved by the original list when scores tie.
    """
    sorted_clips = sorted(
        enumerate(clips),
        key=lambda iv: (-iv[1]["virality_score"], iv[0]),
    )
    kept: list[tuple[int, dict[str, Any]]] = []
    for orig_idx, clip in sorted_clips:
        start, end = clip["start_ms"], clip["end_ms"]
        overlaps = any(
            not (end <= k["start_ms"] or start >= k["end_ms"])
            for _, k in kept
        )
        if not overlaps:
            kept.append((orig_idx, clip))
    # Restore original emission order for deterministic downstream ordering.
    kept.sort(key=lambda iv: iv[0])
    return [c for _, c in kept]


# ---------------------------------------------------------------------------
# Claude call + validation loop
# ---------------------------------------------------------------------------


def _call_and_validate(
    system_blocks: list[dict[str, Any]],
    base_messages: list[dict[str, Any]],
    *,
    job_id: str,
) -> tuple[EpisodeAnalysisSchema, int, int]:
    """Drive the Claude call with the validation-retry loop (SPEC §4.5, §6).

    Returns ``(parsed_model, input_tokens, output_tokens)`` of the successful
    attempt. ``cache_read_tokens`` is logged by the client but not returned
    because the Analysis row doesn't store it.
    """
    messages = list(base_messages)
    last_text = ""
    last_error = ""

    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        # Drop temperature on the final attempt — any randomness left is
        # noise preventing us from matching the schema we've already told
        # Claude twice.
        temp = FINAL_RETRY_TEMPERATURE if attempt == MAX_VALIDATION_ATTEMPTS else TEMPERATURE

        try:
            resp = claude_client.call(
                system=system_blocks,
                messages=messages,
                max_tokens=MAX_TOKENS,
                temperature=temp,
                prompt_name="analysis",
                job_id=job_id,
            )
        except ClaudeError as exc:
            code = "ANALYSIS_SERVICE_DOWN" if exc.transient else "ANALYSIS_INVALID_REQUEST"
            raise AnalysisError(code, str(exc)) from exc

        last_text = resp.text
        stripped = _strip_markdown_fences(resp.text)
        try:
            parsed_dict = json.loads(stripped)
        except json.JSONDecodeError as exc:
            last_error = f"not valid JSON: {exc.msg} at line {exc.lineno} col {exc.colno}"
            logger.warning(
                "analysis_invalid_json",
                extra={"job_id": job_id, "attempt": attempt, "error": last_error},
            )
            if attempt < MAX_VALIDATION_ATTEMPTS:
                messages = list(base_messages) + retry_user_message(last_text, last_error)
            continue

        try:
            parsed = EpisodeAnalysisSchema.model_validate(parsed_dict)
        except ValidationError as exc:
            last_error = f"schema validation failed: {exc.errors()[0]}"
            logger.warning(
                "analysis_schema_invalid",
                extra={"job_id": job_id, "attempt": attempt, "error": last_error},
            )
            if attempt < MAX_VALIDATION_ATTEMPTS:
                messages = list(base_messages) + retry_user_message(last_text, last_error)
            continue

        return parsed, resp.input_tokens, resp.output_tokens

    raise AnalysisError(
        "ANALYSIS_INVALID_JSON",
        f"Claude failed to produce schema-valid JSON after {MAX_VALIDATION_ATTEMPTS} attempts: {last_error}",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_job(job_id: str) -> None:
    """Run Claude analysis for *job_id* and persist the Analysis row.

    Raises ``AnalysisError`` on pipeline-level failures — the Celery task
    layer converts that into a FAILED transition.
    """
    job = Job.objects.select_related("transcript").get(id=job_id)
    transcript: Transcript | None = getattr(job, "transcript", None)
    if transcript is None:
        raise AnalysisError(
            "ANALYSIS_NO_TRANSCRIPT",
            f"Job {job_id} has no transcript; transcription did not complete",
        )

    system_blocks, messages = build_messages(
        full_text=transcript.full_text,
        segments=transcript.segments_json or [],
    )
    parsed, input_tokens, output_tokens = _call_and_validate(
        system_blocks, messages, job_id=job_id
    )

    analysis_dict = parsed.model_dump()
    clip_candidates = _dedupe_overlapping_clips(analysis_dict["clip_candidates"])

    Analysis.objects.update_or_create(
        job=job,
        defaults={
            "episode_title": analysis_dict["episode_title"],
            "hook": analysis_dict["hook"],
            "guest_json": analysis_dict.get("guest"),
            "themes_json": analysis_dict["themes"],
            "chapters_json": analysis_dict["chapters"],
            "clip_candidates_json": clip_candidates,
            "quotes_json": analysis_dict["notable_quotes"],
            "claude_model": settings.CLAUDE_MODEL,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    logger.info(
        "analysis_completed",
        extra={
            "job_id": job_id,
            "title": analysis_dict["episode_title"],
            "clip_count": len(clip_candidates),
            "chapter_count": len(analysis_dict["chapters"]),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
