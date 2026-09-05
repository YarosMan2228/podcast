"""Job listing + deletion — service layer for the history page (Pro).

Views stay HTTP-only (CLAUDE.md: no DB calls in views); everything that
touches rows or the filesystem lives here.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

from jobs.models import Artifact, ArtifactStatus, Job, SourceType

logger = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def create_url_job(url: str, branding: Any = None, clip_options: Any = None) -> Job:
    """Persist a PENDING URL-sourced Job (+ Pro branding / clip options)."""
    from pipeline.branding import store_logo

    with transaction.atomic():
        job = Job.objects.create(
            source_type=SourceType.URL,
            source_url=url,
            podcast_name=getattr(branding, "podcast_name", None),
            brand_color=getattr(branding, "brand_color", None) or "#6366f1",
            clip_layout=getattr(clip_options, "layout", None) or "pad",
            caption_style=getattr(clip_options, "caption_style", None) or "karaoke",
        )
        logo = getattr(branding, "logo", None)
        if logo is not None:
            job.logo_path = store_logo(str(job.id), logo)
            job.save(update_fields=["logo_path"])
    return job


def list_recent_jobs(limit: int = DEFAULT_LIST_LIMIT) -> list[Job]:
    """Newest-first jobs with analysis + artifacts preloaded for summaries."""
    limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    qs: QuerySet[Job] = (
        Job.objects.select_related("analysis")
        .prefetch_related("artifacts")
        .order_by("-created_at")[:limit]
    )
    return list(qs)


def summarize_job(job: Job) -> dict[str, Any]:
    """Compact row for the history list (no artifact bodies)."""
    artifacts: list[Artifact] = list(job.artifacts.all())
    analysis = getattr(job, "analysis", None)
    ready = sum(1 for a in artifacts if a.status == ArtifactStatus.READY)
    failed = sum(1 for a in artifacts if a.status == ArtifactStatus.FAILED)
    return {
        "job_id": str(job.id),
        "status": job.status,
        "source_type": job.source_type,
        "source_url": job.source_url,
        "original_filename": job.original_filename,
        "duration_sec": job.duration_sec,
        "episode_title": analysis.episode_title if analysis else None,
        "hook": analysis.hook if analysis else None,
        "progress": {"total_artifacts": len(artifacts), "ready": ready, "failed": failed},
        "has_package": bool(job.package_path),
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _job_directories(job: Job) -> list[Path]:
    media = Path(settings.MEDIA_ROOT)
    dirs = [
        media / "uploads" / str(job.id),
        Path(settings.ARTIFACTS_ROOT) / str(job.id),
    ]
    # raw_media_path may point somewhere custom (e.g. a test tmp dir) — its
    # parent is the upload folder for this job.
    if job.raw_media_path:
        raw_parent = Path(job.raw_media_path).parent
        if raw_parent.name == str(job.id):
            dirs.append(raw_parent)
    return dirs


def delete_job(job: Job) -> dict[str, int]:
    """Remove the job row (artifacts cascade) and every file it owns.

    Files first, then the row — if a directory refuses to delete we still
    want the row gone so the UI stops listing a zombie; leftovers are
    logged for manual cleanup. Returns counters for the response body.
    """
    removed_dirs = 0
    removed_files = 0

    for d in _job_directories(job):
        try:
            if d.exists():
                shutil.rmtree(d)
                removed_dirs += 1
        except OSError as exc:
            logger.warning(
                "job_delete_dir_failed",
                extra={"job_id": str(job.id), "path": str(d), "error": str(exc)},
            )

    if job.package_path:
        zip_path = Path(job.package_path)
        if not zip_path.is_absolute():
            zip_path = Path(settings.MEDIA_ROOT) / zip_path
        try:
            if zip_path.exists():
                zip_path.unlink()
                removed_files += 1
        except OSError as exc:
            logger.warning(
                "job_delete_zip_failed",
                extra={"job_id": str(job.id), "path": str(zip_path), "error": str(exc)},
            )

    job_id = str(job.id)
    job.delete()
    logger.info(
        "job_deleted",
        extra={"job_id": job_id, "dirs": removed_dirs, "files": removed_files},
    )
    return {"removed_dirs": removed_dirs, "removed_files": removed_files}
