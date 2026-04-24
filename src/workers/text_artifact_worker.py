"""Text artifact Celery workers — SPEC §6.

Five tasks (one per artifact type) in queue ``text_artifacts``.

Each task follows the same scaffold via ``_run_artifact_task``:
  1. Load Artifact + Analysis + Transcript from DB.
  2. Mark artifact PROCESSING.
  3. Build the type-specific prompt.
  4. Call Claude (transcript prompt-cached).
  5. Validate / post-process the response.
  6. Save ``text_content`` + ``metadata_json``, mark READY.
  7. Publish ``artifact_ready`` SSE event.

On transient failure the task retries up to max_retries=3. Only on the
final failure does it write ``status=FAILED`` to the DB.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from core.celery import celery_app
from jobs.models import Artifact, ArtifactStatus
from pipeline.prompts.text_artifacts import (
    DEFAULT_TONES,
    build_linkedin_prompt,
    build_newsletter_prompt,
    build_show_notes_prompt,
    build_twitter_prompt,
    build_youtube_description_prompt,
)
from services.claude_client import call_text_artifact
from services.events import publish

logger = logging.getLogger(__name__)

_TASK_KWARGS: dict[str, Any] = dict(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
    queue="text_artifacts",
)

# ─────────────────────────── helpers ────────────────────────────


def _load_context(artifact_id: str) -> tuple[Artifact, dict, str]:
    """Return (artifact, analysis_dict, transcript_full_text)."""
    artifact = Artifact.objects.select_related(
        "job__analysis", "job__transcript"
    ).get(id=artifact_id)
    analysis = artifact.job.analysis
    transcript = artifact.job.transcript
    analysis_dict: dict[str, Any] = {
        "episode_title": analysis.episode_title,
        "hook": analysis.hook,
        "guest_json": analysis.guest_json,
        "themes_json": analysis.themes_json,
        "chapters_json": analysis.chapters_json,
        "clip_candidates_json": analysis.clip_candidates_json,
        "quotes_json": analysis.quotes_json,
    }
    return artifact, analysis_dict, transcript.full_text


def _mark_processing(artifact: Artifact) -> None:
    Artifact.objects.filter(id=artifact.id).update(status=ArtifactStatus.PROCESSING)


def _mark_ready(
    artifact: Artifact, text_content: str, metadata: dict[str, Any]
) -> None:
    Artifact.objects.filter(id=artifact.id).update(
        status=ArtifactStatus.READY,
        text_content=text_content,
        metadata_json=metadata,
        error=None,
    )
    publish(
        str(artifact.job_id),
        "artifact_ready",
        {
            "artifact_id": str(artifact.id),
            "type": artifact.type,
            "index": artifact.index,
        },
    )


def _mark_failed(artifact: Artifact, error_msg: str) -> None:
    Artifact.objects.filter(id=artifact.id).update(
        status=ArtifactStatus.FAILED,
        error=error_msg,
    )
    publish(
        str(artifact.job_id),
        "artifact_failed",
        {"artifact_id": str(artifact.id), "error": error_msg},
    )


def _count_words(text: str) -> int:
    return len(text.split())


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if Claude wraps the output."""
    return re.sub(r"^```[a-z]*\n?|\n?```$", "", text.strip(), flags=re.MULTILINE).strip()


def _truncate_to_word_limit(text: str, max_words: int) -> str:
    """Cut to the last complete sentence within the word budget."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    last_boundary = max(
        truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?")
    )
    if last_boundary > 0:
        return truncated[: last_boundary + 1]
    return truncated


def _split_tweet_at_limit(tweet: str, limit: int = 270) -> list[str]:
    """Split an over-limit tweet at the last space before the character limit."""
    if len(tweet) <= limit:
        return [tweet]
    split_at = tweet.rfind(" ", 0, limit)
    if split_at == -1:
        split_at = limit
    return [tweet[:split_at].rstrip(), tweet[split_at:].lstrip()]


# ─────────────────────── shared task scaffold ───────────────────────


def _run_artifact_task(
    self: Any,
    artifact_id: str,
    tone: str | None,
    artifact_type_key: str,
    generate_fn: Callable[[dict, str, str], tuple[str, dict]],
) -> None:
    """Shared execution scaffold for all text artifact Celery tasks.

    ``generate_fn(analysis, transcript_text, effective_tone)`` is responsible
    for calling Claude and returning ``(text_content, extra_metadata_dict)``.
    ``extra_metadata_dict`` must include at minimum ``word_count`` plus the
    usage keys from ``call_text_artifact`` (``claude_model``, ``input_tokens``,
    ``output_tokens``).
    """
    artifact: Artifact | None = None
    logger.info(
        "task_started",
        extra={"task": artifact_type_key, "artifact_id": artifact_id},
    )
    try:
        artifact, analysis, transcript_text = _load_context(artifact_id)
        effective_tone = (
            tone
            or artifact.metadata_json.get("tone")
            or DEFAULT_TONES[artifact_type_key]
        )
        _mark_processing(artifact)

        text_content, extra_meta = generate_fn(analysis, transcript_text, effective_tone)
        metadata = {"tone": effective_tone, **extra_meta}
        _mark_ready(artifact, text_content, metadata)

        logger.info(
            "task_completed",
            extra={"task": artifact_type_key, "artifact_id": artifact_id},
        )

    except Exception as exc:
        logger.exception(
            "task_failed",
            extra={"task": artifact_type_key, "artifact_id": artifact_id},
        )
        is_final = self.request.retries >= self.max_retries
        if is_final:
            if artifact is not None:
                try:
                    _mark_failed(artifact, str(exc))
                except Exception:
                    logger.exception(
                        "mark_failed_error",
                        extra={"artifact_id": artifact_id},
                    )
            return
        raise self.retry(exc=exc)


# ─────────────────────────── tasks ──────────────────────────────


@celery_app.task(**_TASK_KWARGS)
def generate_linkedin_post(
    self, artifact_id: str, tone: str | None = None
) -> None:
    def _generate(
        analysis: dict, transcript_text: str, effective_tone: str
    ) -> tuple[str, dict]:
        user_msg = build_linkedin_prompt(analysis, effective_tone)
        content, usage = call_text_artifact(
            transcript_text, user_msg, max_tokens=1024, temperature=0.7
        )
        content = _strip_code_fences(content)

        if _count_words(content) > 500:
            retry_msg = (
                user_msg
                + "\n\nCRITICAL: Your previous response exceeded 500 words. "
                "Rewrite it. MAXIMUM 500 words — count every word carefully."
            )
            content, usage = call_text_artifact(
                transcript_text, retry_msg, max_tokens=1024, temperature=0.5
            )
            content = _strip_code_fences(content)

        if _count_words(content) > 500:
            content = _truncate_to_word_limit(content, 500)

        return content, {"word_count": _count_words(content), **usage}

    _run_artifact_task(self, artifact_id, tone, "LINKEDIN_POST", _generate)


@celery_app.task(**_TASK_KWARGS)
def generate_twitter_thread(
    self, artifact_id: str, tone: str | None = None
) -> None:
    def _generate(
        analysis: dict, transcript_text: str, effective_tone: str
    ) -> tuple[str, dict]:
        user_msg = build_twitter_prompt(analysis, effective_tone)
        content, usage = call_text_artifact(
            transcript_text, user_msg, max_tokens=2048, temperature=0.7
        )
        content = _strip_code_fences(content)

        # Parse JSON; retry once on invalid JSON
        try:
            data = json.loads(content)
            tweets: list[str] = data["tweets"]
        except (json.JSONDecodeError, KeyError):
            retry_msg = (
                user_msg
                + "\n\nCRITICAL: Your previous response was not valid JSON. "
                'Return ONLY the JSON object {"tweets": [...]} — nothing else.'
            )
            content, usage = call_text_artifact(
                transcript_text, retry_msg, max_tokens=2048, temperature=0.3
            )
            data = json.loads(_strip_code_fences(content))
            tweets = data["tweets"]

        # Fix any tweets that exceed 280 chars (split at word boundary)
        fixed: list[str] = []
        for tweet in tweets:
            fixed.extend(_split_tweet_at_limit(tweet, limit=270))

        tweets_json = json.dumps({"tweets": fixed}, ensure_ascii=False)
        word_count = sum(_count_words(t) for t in fixed)
        return tweets_json, {"tweet_count": len(fixed), "word_count": word_count, **usage}

    _run_artifact_task(self, artifact_id, tone, "TWITTER_THREAD", _generate)


@celery_app.task(**_TASK_KWARGS)
def generate_show_notes(
    self, artifact_id: str, tone: str | None = None
) -> None:
    def _generate(
        analysis: dict, transcript_text: str, effective_tone: str
    ) -> tuple[str, dict]:
        user_msg = build_show_notes_prompt(analysis, effective_tone)
        content, usage = call_text_artifact(
            transcript_text, user_msg, max_tokens=2048, temperature=0.5
        )
        content = _strip_code_fences(content)
        return content, {"word_count": _count_words(content), **usage}

    _run_artifact_task(self, artifact_id, tone, "SHOW_NOTES", _generate)


@celery_app.task(**_TASK_KWARGS)
def generate_newsletter(
    self, artifact_id: str, tone: str | None = None
) -> None:
    def _generate(
        analysis: dict, transcript_text: str, effective_tone: str
    ) -> tuple[str, dict]:
        user_msg = build_newsletter_prompt(analysis, effective_tone)
        content, usage = call_text_artifact(
            transcript_text, user_msg, max_tokens=1536, temperature=0.7
        )
        content = _strip_code_fences(content)
        return content, {"word_count": _count_words(content), **usage}

    _run_artifact_task(self, artifact_id, tone, "NEWSLETTER", _generate)


@celery_app.task(**_TASK_KWARGS)
def generate_youtube_description(
    self, artifact_id: str, tone: str | None = None
) -> None:
    def _generate(
        analysis: dict, transcript_text: str, effective_tone: str
    ) -> tuple[str, dict]:
        user_msg = build_youtube_description_prompt(analysis, effective_tone)
        content, usage = call_text_artifact(
            transcript_text, user_msg, max_tokens=1024, temperature=0.5
        )
        content = _strip_code_fences(content)
        return content, {"word_count": _count_words(content), **usage}

    _run_artifact_task(self, artifact_id, tone, "YOUTUBE_DESCRIPTION", _generate)
