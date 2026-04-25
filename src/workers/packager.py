"""Packaging worker — SPEC §8.

Builds a ZIP with ``clips/``, ``text/``, ``graphics/`` folders + an
``index.txt`` summary, then flips the Job to ``COMPLETED`` and emits the
``completed`` SSE event with the download URL.

Triggered by :func:`workers.tasks.check_and_trigger_packaging` once every
artifact is in a terminal state. Designed to be idempotent — a second
invocation on a job already in ``COMPLETED`` is a no-op.

Failure-tolerance (SPEC §9.5):

* If at least one artifact is ``READY``, we still build the package and
  note the failed ones in ``index.txt`` — the user gets a partial pack
  rather than a blocked job.
* If zero artifacts succeeded, the job is moved to ``FAILED`` instead of
  ``COMPLETED``.
"""
from __future__ import annotations

import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.utils import timezone as djtz

from core.celery import celery_app
from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
)
from services.events import publish

logger = logging.getLogger(__name__)


# Mapping ArtifactType → (folder inside zip, filename template).
# ``{index}`` is filled with ``artifact.index``; ``{ext}`` with the source
# file's extension (so we don't lie about the format in the zip).
_ARCHIVE_LAYOUT: dict[str, tuple[str, str]] = {
    ArtifactType.VIDEO_CLIP: ("clips", "clip_{index}{ext}"),
    ArtifactType.LINKEDIN_POST: ("text", "linkedin.md"),
    ArtifactType.TWITTER_THREAD: ("text", "twitter_thread.md"),
    ArtifactType.SHOW_NOTES: ("text", "show_notes.md"),
    ArtifactType.NEWSLETTER: ("text", "newsletter.md"),
    ArtifactType.YOUTUBE_DESCRIPTION: ("text", "youtube_description.txt"),
    ArtifactType.QUOTE_GRAPHIC: ("graphics", "quote_{index}{ext}"),
    ArtifactType.EPISODE_THUMBNAIL: ("graphics", "thumbnail{ext}"),
}


def _archive_name(artifact: Artifact, *, ext_fallback: str = "") -> str:
    """Resolve the path inside the zip for *artifact*.

    Returns ``"<folder>/<file>"`` per ``_ARCHIVE_LAYOUT``. Unknown types
    fall back to ``misc/<id>`` so we never silently drop content.
    """
    template = _ARCHIVE_LAYOUT.get(artifact.type)
    if template is None:
        return f"misc/{artifact.id}"
    folder, name_tpl = template
    ext = ""
    if artifact.file_path:
        ext = Path(artifact.file_path).suffix or ext_fallback
    return f"{folder}/{name_tpl.format(index=artifact.index, ext=ext)}"


def _resolve_artifact_file(artifact: Artifact) -> Path | None:
    """Locate the on-disk source file for an artifact, or None.

    ``Artifact.file_path`` is stored as a relative path under
    ``MEDIA_ROOT`` by every worker that produces files (see
    ``video_clip_worker``, ``quote_graphic_worker``). Absolute paths are
    accepted as-is for forward-compat.
    """
    if not artifact.file_path:
        return None
    p = Path(artifact.file_path)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / p
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# index.txt rendering
# ---------------------------------------------------------------------------


_TYPE_LABELS: dict[str, str] = {
    ArtifactType.VIDEO_CLIP: "Video clips",
    ArtifactType.LINKEDIN_POST: "LinkedIn post",
    ArtifactType.TWITTER_THREAD: "Twitter thread",
    ArtifactType.SHOW_NOTES: "Show notes",
    ArtifactType.NEWSLETTER: "Newsletter",
    ArtifactType.YOUTUBE_DESCRIPTION: "YouTube description",
    ArtifactType.QUOTE_GRAPHIC: "Quote graphics",
    ArtifactType.EPISODE_THUMBNAIL: "Thumbnail",
}


def render_index_txt(
    job: Job,
    analysis: Analysis | None,
    artifacts: Iterable[Artifact],
) -> str:
    """Build the human-readable summary that ships at the root of the zip."""
    lines: list[str] = []
    lines.append("=== Podcast Content Pack ===")
    lines.append("")
    if analysis is not None:
        lines.append(f"Episode: {analysis.episode_title or '(untitled)'}")
        if analysis.hook:
            lines.append(f"Hook:    {analysis.hook}")
    lines.append(f"Job ID:  {job.id}")
    lines.append(
        f"Built:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    lines.append("")
    lines.append("Contents")
    lines.append("--------")

    grouped: dict[str, list[Artifact]] = {}
    for art in artifacts:
        grouped.setdefault(art.type, []).append(art)

    # Stable order so the index is deterministic for tests + UX.
    for type_key in _TYPE_LABELS:
        items = grouped.get(type_key) or []
        if not items:
            continue
        items.sort(key=lambda a: a.index)
        lines.append("")
        lines.append(_TYPE_LABELS[type_key])
        for art in items:
            archive = _archive_name(art)
            if art.status == ArtifactStatus.READY:
                lines.append(f"  - {archive}")
            else:
                detail = art.error or art.status
                lines.append(f"  - {archive}  [SKIPPED: {detail}]")

    # Missing types — call them out explicitly so the user knows what's
    # absent rather than guessing from the empty folder.
    missing = [t for t in _TYPE_LABELS if t not in grouped]
    if missing:
        lines.append("")
        lines.append("Missing")
        lines.append("-------")
        for t in missing:
            lines.append(f"  - {_TYPE_LABELS[t]}")

    lines.append("")
    lines.append("How to use")
    lines.append("----------")
    lines.append("  clips/      — vertical 9:16 mp4, ready for TikTok / Reels / Shorts.")
    lines.append("  text/       — markdown drafts; tweak voice and post.")
    lines.append("  graphics/   — 1080x1080 PNGs for Instagram / LinkedIn carousels.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Zip assembly
# ---------------------------------------------------------------------------


def _build_zip(
    job: Job,
    analysis: Analysis | None,
    artifacts: list[Artifact],
    output_path: Path,
) -> tuple[int, int]:
    """Write the package zip; return ``(included_count, skipped_count)``.

    Skipped = artifacts that should appear in the layout but had no usable
    file/text on disk (FAILED, missing file, empty content). They still
    show up in ``index.txt`` so the user understands why something is
    missing rather than seeing a quiet empty folder.
    """
    included = 0
    skipped = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.txt", render_index_txt(job, analysis, artifacts))

        for art in artifacts:
            archive = _archive_name(art)
            if art.status != ArtifactStatus.READY:
                skipped += 1
                continue

            # Text-content artifacts (LinkedIn, Twitter, ...).
            if art.text_content:
                zf.writestr(archive, art.text_content)
                included += 1
                continue

            # File-backed artifacts (video clips, quote graphics, thumbnail).
            src = _resolve_artifact_file(art)
            if src is None:
                logger.warning(
                    "package_artifact_missing_file",
                    extra={
                        "job_id": str(job.id),
                        "artifact_id": str(art.id),
                        "type": art.type,
                        "file_path": art.file_path,
                    },
                )
                skipped += 1
                continue
            zf.write(src, archive)
            included += 1

    return included, skipped


# ---------------------------------------------------------------------------
# Status / event helpers (kept thin; tasks.py owns the global transition rules)
# ---------------------------------------------------------------------------


def _safe_transition(job_id: str, to_status: str) -> bool:
    """Run a SPEC §1.1 transition without bouncing tests through Celery.

    Imports lazily so the packager is testable in isolation — the
    ``transition_job_status`` helper in ``workers.tasks`` couples this to
    the rest of the orchestration layer.
    """
    from workers.tasks import InvalidTransition, transition_job_status

    job = Job.objects.get(id=job_id)
    try:
        transition_job_status(job_id, job.status, to_status)
    except InvalidTransition as exc:
        logger.warning(
            "package_transition_skipped",
            extra={
                "job_id": str(job_id),
                "to_status": to_status,
                "error": str(exc),
            },
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Celery entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
)
def package_job(self, job_id: str) -> None:
    """Assemble the ZIP and finalize the Job (SPEC §8.2)."""
    logger.info("task_started", extra={"task": "package_job", "job_id": job_id})

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        logger.warning(
            "package_job_unknown_job_id", extra={"job_id": str(job_id)}
        )
        return

    # Idempotency: re-firing on a finalized job is fine (see §9.5).
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        logger.info(
            "package_job_already_terminal",
            extra={"job_id": str(job_id), "status": job.status},
        )
        return

    if not _safe_transition(job_id, JobStatus.PACKAGING):
        return

    artifacts = list(Artifact.objects.filter(job_id=job_id).order_by("type", "index"))
    if not artifacts:
        # Orchestrator created no artifacts — fail loudly rather than ship
        # an empty zip that the user would think is broken.
        Job.objects.filter(id=job_id).update(
            error="PACKAGE_EMPTY: no artifacts to package"
        )
        _safe_transition(job_id, JobStatus.FAILED)
        return

    ready_count = sum(1 for a in artifacts if a.status == ArtifactStatus.READY)
    if ready_count == 0:
        Job.objects.filter(id=job_id).update(
            error="PACKAGE_ALL_FAILED: every artifact is in FAILED state"
        )
        _safe_transition(job_id, JobStatus.FAILED)
        return

    analysis = Analysis.objects.filter(job_id=job_id).first()

    # ZIP location: MEDIA_ROOT/packages/podcast_pack_<short>_<ts>.zip — the
    # short id keeps the filename grep-friendly without leaking the full uuid.
    short_id = str(job.id).split("-")[0]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rel_zip = Path("packages") / f"podcast_pack_{short_id}_{timestamp}.zip"
    abs_zip = Path(settings.MEDIA_ROOT) / rel_zip

    try:
        included, skipped = _build_zip(job, analysis, artifacts, abs_zip)
    except (OSError, zipfile.BadZipFile) as exc:
        logger.exception(
            "package_job_zip_failed", extra={"job_id": str(job_id)}
        )
        Job.objects.filter(id=job_id).update(error=f"PACKAGE_IO_ERROR: {exc}")
        _safe_transition(job_id, JobStatus.FAILED)
        return

    Job.objects.filter(id=job_id).update(
        package_path=str(rel_zip),
        completed_at=djtz.now(),
    )
    if not _safe_transition(job_id, JobStatus.COMPLETED):
        return

    media_url = settings.MEDIA_URL or "/media/"
    if not media_url.endswith("/"):
        media_url += "/"
    package_url = media_url + str(rel_zip).replace("\\", "/")

    publish(
        str(job_id),
        "completed",
        {"package_url": package_url},
    )

    logger.info(
        "task_completed",
        extra={
            "task": "package_job",
            "job_id": str(job_id),
            "included": included,
            "skipped": skipped,
            "package_path": str(rel_zip),
        },
    )
