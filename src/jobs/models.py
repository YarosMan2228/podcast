"""Django's AppConfig auto-imports `<app>.models`, so this is the entry point.

Actual model classes live in sibling submodules (`job.py`, `transcript.py`,
`analysis.py`, `artifact.py`). Importing them here registers each with the
Django app registry and gives callers a single tidy import path:

    from jobs.models import Job, Transcript, Analysis, Artifact
"""
from jobs.analysis import Analysis
from jobs.artifact import Artifact
from jobs.enums import (
    JOB_TRANSITIONS,
    ArtifactStatus,
    ArtifactType,
    JobStatus,
    SourceType,
    can_transition,
)
from jobs.job import Job
from jobs.transcript import Transcript

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
