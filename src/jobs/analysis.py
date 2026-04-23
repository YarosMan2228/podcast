"""Analysis model — SPEC §4.2 `analyses` table."""
from __future__ import annotations

import uuid

from django.db import models as djmodels

from jobs.job import Job


class Analysis(djmodels.Model):
    id = djmodels.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = djmodels.OneToOneField(
        Job,
        on_delete=djmodels.CASCADE,
        related_name="analysis",
    )
    episode_title = djmodels.CharField(max_length=255)
    hook = djmodels.TextField()
    guest_json = djmodels.JSONField(null=True, blank=True)
    themes_json = djmodels.JSONField(default=list)
    chapters_json = djmodels.JSONField(default=list)
    clip_candidates_json = djmodels.JSONField(default=list)
    quotes_json = djmodels.JSONField(default=list)
    claude_model = djmodels.CharField(max_length=64)
    input_tokens = djmodels.IntegerField()
    output_tokens = djmodels.IntegerField()
    created_at = djmodels.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "analyses"

    def __str__(self) -> str:
        return f"Analysis(job={self.job_id} · {self.episode_title!r})"
