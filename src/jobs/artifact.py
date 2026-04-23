"""Artifact model — SPEC §5.2 `artifacts` table.

Shared between Person A (VIDEO_CLIP) and Person B (text/graphic types).
"""
from __future__ import annotations

import uuid

from django.db import models as djmodels

from jobs.enums import ArtifactStatus, ArtifactType
from jobs.job import Job
from jobs.managers import ArtifactManager


class Artifact(djmodels.Model):
    id = djmodels.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = djmodels.ForeignKey(Job, on_delete=djmodels.CASCADE, related_name="artifacts")
    type = djmodels.CharField(max_length=32, choices=ArtifactType.choices)
    status = djmodels.CharField(
        max_length=16,
        choices=ArtifactStatus.choices,
        default=ArtifactStatus.QUEUED,
    )
    index = djmodels.IntegerField()
    file_path = djmodels.TextField(null=True, blank=True)
    text_content = djmodels.TextField(null=True, blank=True)
    metadata_json = djmodels.JSONField(default=dict)
    version = djmodels.IntegerField(default=1)
    error = djmodels.TextField(null=True, blank=True)
    created_at = djmodels.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = djmodels.DateTimeField(auto_now=True)

    objects = ArtifactManager()

    class Meta:
        db_table = "artifacts"
        constraints = [
            djmodels.UniqueConstraint(
                fields=["job", "type", "index"],
                name="uniq_artifact_job_type_index",
            ),
        ]
        indexes = [
            djmodels.Index(fields=["job", "status"]),
        ]

    def __str__(self) -> str:
        return f"Artifact({self.type}#{self.index} · {self.status})"
