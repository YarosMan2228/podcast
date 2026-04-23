"""Job model — SPEC §2.2 `jobs` table."""
from __future__ import annotations

import uuid

from django.db import models as djmodels

from jobs.enums import JobStatus, SourceType


class Job(djmodels.Model):
    id = djmodels.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = djmodels.CharField(
        max_length=32,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    source_type = djmodels.CharField(max_length=16, choices=SourceType.choices)
    source_url = djmodels.TextField(null=True, blank=True)
    original_filename = djmodels.CharField(max_length=255, null=True, blank=True)
    raw_media_path = djmodels.TextField(null=True, blank=True)
    normalized_wav_path = djmodels.TextField(null=True, blank=True)
    duration_sec = djmodels.FloatField(null=True, blank=True)
    file_size_bytes = djmodels.BigIntegerField(null=True, blank=True)
    mime_type = djmodels.CharField(max_length=64, null=True, blank=True)
    error = djmodels.TextField(null=True, blank=True)
    created_at = djmodels.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = djmodels.DateTimeField(auto_now=True)
    completed_at = djmodels.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "jobs"
        indexes = [
            djmodels.Index(fields=["status", "created_at"]),
            djmodels.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Job({self.id} · {self.status})"
