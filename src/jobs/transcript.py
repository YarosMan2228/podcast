"""Transcript model — SPEC §3.2 `transcripts` table."""
from __future__ import annotations

import uuid

from django.db import models as djmodels

from jobs.job import Job


class Transcript(djmodels.Model):
    id = djmodels.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # OneToOne enforces SPEC's UNIQUE(job_id) — one transcript per job.
    job = djmodels.OneToOneField(
        Job,
        on_delete=djmodels.CASCADE,
        related_name="transcript",
    )
    language = djmodels.CharField(max_length=8)
    full_text = djmodels.TextField()
    segments_json = djmodels.JSONField(default=list)
    whisper_model = djmodels.CharField(max_length=32, default="whisper-1")
    duration_sec = djmodels.FloatField()
    created_at = djmodels.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transcripts"

    def __str__(self) -> str:
        return f"Transcript(job={self.job_id} · {self.language})"
