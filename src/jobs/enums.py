"""Enums from SPEC §§1.1–1.3.

`TextChoices` gives us grep-friendly string values in DB (`status=PENDING`
instead of an opaque int) plus a CHECK-like constraint via Django's `choices`.
"""
from __future__ import annotations

from django.db import models


class JobStatus(models.TextChoices):
    PENDING = "PENDING"
    INGESTING = "INGESTING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    GENERATING = "GENERATING"
    PACKAGING = "PACKAGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SourceType(models.TextChoices):
    FILE = "file"
    URL = "url"


class ArtifactType(models.TextChoices):
    VIDEO_CLIP = "VIDEO_CLIP"
    LINKEDIN_POST = "LINKEDIN_POST"
    TWITTER_THREAD = "TWITTER_THREAD"
    SHOW_NOTES = "SHOW_NOTES"
    NEWSLETTER = "NEWSLETTER"
    QUOTE_GRAPHIC = "QUOTE_GRAPHIC"
    EPISODE_THUMBNAIL = "EPISODE_THUMBNAIL"
    YOUTUBE_DESCRIPTION = "YOUTUBE_DESCRIPTION"


class ArtifactStatus(models.TextChoices):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


# Legal Job status transitions per SPEC §1.1.
# Any state → FAILED is always allowed; COMPLETED/FAILED are terminal.
JOB_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.PENDING.value: {JobStatus.INGESTING.value, JobStatus.FAILED.value},
    JobStatus.INGESTING.value: {JobStatus.TRANSCRIBING.value, JobStatus.FAILED.value},
    JobStatus.TRANSCRIBING.value: {JobStatus.ANALYZING.value, JobStatus.FAILED.value},
    JobStatus.ANALYZING.value: {JobStatus.GENERATING.value, JobStatus.FAILED.value},
    JobStatus.GENERATING.value: {JobStatus.PACKAGING.value, JobStatus.FAILED.value},
    JobStatus.PACKAGING.value: {JobStatus.COMPLETED.value, JobStatus.FAILED.value},
    JobStatus.COMPLETED.value: set(),
    JobStatus.FAILED.value: set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in JOB_TRANSITIONS.get(current, set())
