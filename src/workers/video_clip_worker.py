"""Celery worker that renders a single VIDEO_CLIP Artifact (SPEC §5).

Flow (one call = one artifact):

1. Load Artifact → Job → Analysis → Transcript.
2. Pick a ``clip_candidate`` (fresh generation = ``candidates[artifact.index]``;
   regenerate = next unused index, SPEC §5.4).
3. Clamp the window to the episode duration (SPEC §5.5 edge case).
4. Build an ASS karaoke subtitle file from the transcript's word-level
   timestamps and write it to a temp path (ffmpeg-usage.md §5).
5. Invoke ``pipeline.ffmpeg_clip.build_vertical_clip`` — writes the mp4
   under ``{ARTIFACTS_ROOT}/{job_id}/clip_{index}_v{version}.mp4``.
6. Update the Artifact row (status=READY, file_path, metadata) and publish
   an ``artifact_ready`` SSE event.

Any ``FFmpegClipError`` or validation failure marks the Artifact FAILED
and publishes ``artifact_failed`` — the orchestrator in Day-4 will decide
whether the overall Job can still complete (SPEC §9.5: a single failed
clip doesn't kill the job).
"""
from __future__ import annotations

import logging
import os
import random
import tempfile
import uuid
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from core.celery import celery_app
from jobs.models import Artifact, ArtifactStatus, ArtifactType, Job
from pipeline.ass_subtitles import build_ass, words_from_segments
from pipeline.ffmpeg_clip import FFmpegClipError, build_vertical_clip
from services.events import publish

logger = logging.getLogger(__name__)


# SPEC §5.5: if clamping the window drops duration below this, we FAIL
# rather than emit a 5-second stub — the next regenerate call will pick
# a different candidate.
MIN_CLIP_DURATION_SEC = 20
# Regenerate offset when all candidates are exhausted (SPEC §5.4).
REGEN_OFFSET_MS_MAX = 3000
# SPEC §5.5: "FFmpeg падает с Invalid data → Retry 1 раз; если снова → FAILED".
# Cap independent of Celery's task-level max_retries (which covers all error
# classes) so a flaky stderr can't loop forever.
MAX_FFMPEG_TRANSIENT_RETRIES = 1

# Audio-only MIME sniffing — kept small on purpose. A mis-classified video
# as audio would just look weird (waveform instead of picture), not break.
_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {"mp3", "wav", "m4a", "ogg", "flac", "opus", "aac"}
)


# ---------------------------------------------------------------------------
# Source-type detection + candidate selection
# ---------------------------------------------------------------------------


def _is_audio_only(job: Job) -> bool:
    """Heuristic: mime prefix wins, fall back to extension sniffing.

    Used only to decide which ffmpeg filter graph to run — not a security
    check, so false positives just pick the waveform renderer.
    """
    if job.mime_type and job.mime_type.startswith("audio/"):
        return True
    if job.raw_media_path:
        ext = Path(job.raw_media_path).suffix.lower().lstrip(".")
        if ext in _AUDIO_EXTENSIONS:
            return True
    return False


def _pick_candidate(
    candidates: list[dict[str, Any]],
    metadata_json: dict[str, Any],
    artifact_index: int,
    regenerate: bool,
) -> tuple[dict[str, Any], int]:
    """Pick a clip candidate and return ``(candidate, used_index)``.

    Fresh generation always uses ``candidates[artifact_index]``. Regenerate
    looks for the next unused index; if none left, reuses the initial one
    with a random offset applied at clamp time (see :func:`_clamp_window`).
    """
    if not candidates:
        raise ValueError("no clip candidates available")

    used = list(metadata_json.get("used_candidate_indices") or [])

    if not regenerate:
        idx = artifact_index if artifact_index < len(candidates) else 0
        return candidates[idx], idx

    for idx, cand in enumerate(candidates):
        if idx not in used:
            return cand, idx
    # All used — reuse the first (will have its start offset shifted later).
    fallback_idx = used[0] if used else 0
    fallback_idx = fallback_idx if fallback_idx < len(candidates) else 0
    return candidates[fallback_idx], fallback_idx


def _clamp_window(
    candidate: dict[str, Any],
    episode_duration_sec: float | None,
    *,
    apply_random_offset: bool,
) -> tuple[int, int]:
    """Clamp ``[start_ms, end_ms]`` to the episode and optionally jitter.

    Returns the final ``(start_ms, end_ms)``. Raises ``ValueError`` if the
    resulting window is shorter than ``MIN_CLIP_DURATION_SEC`` (SPEC §5.5).
    """
    start_ms = int(candidate["start_ms"])
    end_ms = int(candidate["end_ms"])

    if apply_random_offset:
        shift = random.randint(-REGEN_OFFSET_MS_MAX, REGEN_OFFSET_MS_MAX)
        start_ms = max(0, start_ms + shift)
        end_ms = end_ms + shift

    if episode_duration_sec is not None:
        episode_ms = int(episode_duration_sec * 1000)
        end_ms = min(end_ms, episode_ms)
        start_ms = min(start_ms, max(0, episode_ms - 1))

    if end_ms - start_ms < MIN_CLIP_DURATION_SEC * 1000:
        raise ValueError(
            f"clip window too short after clamp: "
            f"{(end_ms - start_ms) / 1000:.1f}s < {MIN_CLIP_DURATION_SEC}s"
        )
    return start_ms, end_ms


# ---------------------------------------------------------------------------
# Rendering — pure function over an Artifact row
# ---------------------------------------------------------------------------


def _render_clip(artifact: Artifact, regenerate: bool) -> None:
    """Render the mp4 for *artifact*. Mutates the row in place.

    Raises on any precondition failure or ffmpeg error. The Celery task
    wrapper is responsible for catching and flipping the Artifact to
    FAILED — keeping this function "raise-on-error" lets the happy path
    read top-to-bottom without try/except noise.
    """
    job = Job.objects.select_related("analysis", "transcript").get(id=artifact.job_id)

    analysis = getattr(job, "analysis", None)
    transcript = getattr(job, "transcript", None)
    if analysis is None:
        raise ValueError(f"Job {job.id} has no analysis row")
    if transcript is None:
        raise ValueError(f"Job {job.id} has no transcript row")

    candidates = list(analysis.clip_candidates_json or [])
    metadata = dict(artifact.metadata_json or {})
    candidate, used_index = _pick_candidate(
        candidates, metadata, artifact.index, regenerate
    )

    used_indices = list(metadata.get("used_candidate_indices") or [])
    exhausted = regenerate and used_index in used_indices
    start_ms, end_ms = _clamp_window(
        candidate,
        job.duration_sec,
        apply_random_offset=exhausted,
    )

    # --- Subtitles: write ASS to a temp file (ffmpeg-usage.md §5) ---
    words = words_from_segments(transcript.segments_json or [])
    ass_path: str | None = os.path.join(
        tempfile.gettempdir(), f"sub_{uuid.uuid4().hex}.ass"
    )
    ass_content = build_ass(words, start_ms, end_ms)
    # If no words fell inside the window, skip the subtitles filter entirely —
    # an ASS file with zero Dialogue lines still renders, but we save a
    # filesystem round-trip and make the failure mode explicit in logs.
    in_clip_word_count = ass_content.count("Dialogue:")
    if in_clip_word_count == 0:
        logger.warning(
            "clip_no_subtitles",
            extra={"job_id": str(job.id), "artifact_id": str(artifact.id)},
        )
        ass_path = None
    else:
        with open(ass_path, "w", encoding="utf-8") as fh:
            fh.write(ass_content)

    # --- Output path + ffmpeg invocation ---
    output_dir = Path(settings.ARTIFACTS_ROOT) / str(job.id)
    output_path = output_dir / (
        f"clip_{artifact.index}_v{artifact.version}.mp4"
    )

    source_path = job.raw_media_path or job.normalized_wav_path
    if not source_path:
        raise ValueError(f"Job {job.id} has no media on disk to clip from")

    try:
        build_vertical_clip(
            input_media_path=source_path,
            start_ms=start_ms,
            end_ms=end_ms,
            ass_path=ass_path,
            output_path=str(output_path),
            audio_only=_is_audio_only(job),
            job_id=str(job.id),
        )
    finally:
        # Always clean up the temp .ass, even on ffmpeg failure.
        if ass_path and os.path.exists(ass_path):
            try:
                os.remove(ass_path)
            except OSError:
                logger.warning("ass_cleanup_failed", extra={"path": ass_path})

    # --- Persist result ---
    file_size = output_path.stat().st_size if output_path.exists() else 0
    duration_sec = (end_ms - start_ms) / 1000.0

    if used_index not in used_indices:
        used_indices.append(used_index)

    metadata.update(
        {
            "source_clip_candidate_index": used_index,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_sec": duration_sec,
            "hook_text": candidate.get("hook_text", ""),
            "virality_score": candidate.get("virality_score"),
            "file_size_bytes": file_size,
            "resolution": "1080x1920",
            "captions_style": "karaoke_white_yellow",
            "used_candidate_indices": used_indices,
        }
    )

    # Relative path under MEDIA_ROOT so the Day-4 SSE payload / download
    # endpoint can compose a URL without caring about the absolute root.
    rel_path = os.path.relpath(str(output_path), settings.MEDIA_ROOT)

    Artifact.objects.filter(id=artifact.id).update(
        status=ArtifactStatus.READY,
        file_path=rel_path.replace("\\", "/"),
        metadata_json=metadata,
        error=None,
    )


# ---------------------------------------------------------------------------
# Celery task wrapper
# ---------------------------------------------------------------------------


def _mark_failed(artifact_id: str, job_id: str, code: str, message: str) -> None:
    """Persist + announce a clip failure (no raise; rules/celery-tasks.md §6)."""
    Artifact.objects.filter(id=artifact_id).update(
        status=ArtifactStatus.FAILED,
        error=f"{code}: {message}",
    )
    publish(
        str(job_id),
        "artifact_failed",
        {"artifact_id": str(artifact_id), "error": f"{code}: {message}"},
    )
    logger.warning(
        "video_clip_failed",
        extra={
            "artifact_id": str(artifact_id),
            "job_id": str(job_id),
            "code": code,
            "error_message": message,
        },
    )
    # SPEC §9.4: a terminal artifact may be the last one — try packaging.
    from workers.tasks import check_and_trigger_packaging
    check_and_trigger_packaging(str(job_id))


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
    queue="video",
)
def generate_video_clip(self, artifact_id: str, regenerate: bool = False) -> None:
    """Render one VIDEO_CLIP Artifact. Idempotent on success and failure.

    Pre-checks the artifact exists and is of type VIDEO_CLIP; flips status
    QUEUED/FAILED → PROCESSING before calling the pipeline. Regenerate
    bumps ``version`` at task start so the output filename differs from
    the previous one.
    """
    logger.info(
        "task_started",
        extra={
            "task": "generate_video_clip",
            "artifact_id": str(artifact_id),
            "regenerate": regenerate,
        },
    )

    try:
        artifact = Artifact.objects.get(id=artifact_id)
    except Artifact.DoesNotExist:
        logger.error(
            "video_clip_missing_artifact",
            extra={"artifact_id": str(artifact_id)},
        )
        return

    if artifact.type != ArtifactType.VIDEO_CLIP:
        _mark_failed(
            str(artifact.id),
            str(artifact.job_id),
            "CLIP_WRONG_TYPE",
            f"Artifact {artifact.id} is {artifact.type}, not VIDEO_CLIP",
        )
        return

    # Flip status (and bump version on regen) in one UPDATE so a second
    # kick against an in-flight artifact won't double-render.
    new_version = artifact.version + 1 if regenerate else artifact.version
    Artifact.objects.filter(id=artifact.id).update(
        status=ArtifactStatus.PROCESSING,
        version=new_version,
        error=None,
    )
    artifact.refresh_from_db()

    try:
        _render_clip(artifact, regenerate=regenerate)
    except SoftTimeLimitExceeded as exc:
        # SPEC §9.5: soft_time_limit hit — kill the artifact rather than
        # leaving it stuck in PROCESSING (which would block packaging).
        # No retry: a 5-min ffmpeg run is unlikely to finish in another 5.
        _mark_failed(
            str(artifact.id),
            str(artifact.job_id),
            "CLIP_TIMEOUT",
            f"soft_time_limit ({generate_video_clip.soft_time_limit}s) exceeded",
        )
        return
    except FFmpegClipError as exc:
        # SPEC §5.5: certain ffmpeg failures (Invalid data, transient IO)
        # recover on a single retry; permanent failures (binary missing,
        # invalid range, output too small) do not.
        if exc.transient and self.request.retries < MAX_FFMPEG_TRANSIENT_RETRIES:
            logger.warning(
                "video_clip_transient_retry",
                extra={
                    "artifact_id": str(artifact.id),
                    "job_id": str(artifact.job_id),
                    "code": exc.code,
                    "attempt": self.request.retries + 1,
                },
            )
            # Reset to QUEUED so the UI shows "queued" rather than "processing"
            # while we wait for the retry — and so a stale PROCESSING row
            # doesn't trip up check_and_trigger_packaging if the retry is
            # delayed past peer artifacts finishing.
            Artifact.objects.filter(id=artifact.id).update(
                status=ArtifactStatus.QUEUED, error=None
            )
            raise self.retry(exc=exc, countdown=2)
        _mark_failed(str(artifact.id), str(artifact.job_id), exc.code, exc.message)
        return
    except ValueError as exc:
        _mark_failed(
            str(artifact.id), str(artifact.job_id), "CLIP_INVALID_INPUT", str(exc)
        )
        return

    publish(
        str(artifact.job_id),
        "artifact_ready",
        {
            "artifact_id": str(artifact.id),
            "type": ArtifactType.VIDEO_CLIP,
            "index": artifact.index,
        },
    )
    from workers.tasks import check_and_trigger_packaging
    check_and_trigger_packaging(str(artifact.job_id))
    logger.info(
        "task_completed",
        extra={
            "task": "generate_video_clip",
            "artifact_id": str(artifact.id),
            "job_id": str(artifact.job_id),
        },
    )
