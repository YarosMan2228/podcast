"""Django's AppConfig auto-imports `<app>.models`, so that's the entry point.

Actual model classes live in submodules (`job.py`, `transcript.py`, ...).
Importing them here registers each with Django's app registry.
"""
from models.analysis import Analysis
from models.artifact import Artifact
from models.enums import (
    JOB_TRANSITIONS,
    ArtifactStatus,
    ArtifactType,
    JobStatus,
    SourceType,
    can_transition,
)
from models.job import Job
from models.transcript import Transcript

__all__ = [
    "Job",
    "Transcript",
    "Analysis",
    "Artifact",
    "JobStatus",
    "SourceType",
    "ArtifactType",
    "ArtifactStatus",
    "JOB_TRANSITIONS",
    "can_transition",
]
