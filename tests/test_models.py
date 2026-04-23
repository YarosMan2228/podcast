"""Model tests — schema, defaults, constraints, cascades, enum helpers.

These also double as a smoke test that migration 0001_initial applies cleanly
on an empty DB (pytest-django creates a fresh DB from migrations).
"""
from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, transaction

from jobs.models import (
    JOB_TRANSITIONS,
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
    Transcript,
    can_transition,
)

pytestmark = pytest.mark.django_db


# -------------------- Job --------------------


def test_job_defaults_and_uuid_pk() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, original_filename="a.mp3")
    assert isinstance(job.id, uuid.UUID)
    assert job.status == JobStatus.PENDING
    assert job.created_at is not None
    assert job.updated_at is not None
    assert job.completed_at is None


def test_job_str_is_useful() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    assert str(job.id) in str(job)
    assert "PENDING" in str(job)


def test_job_url_source_keeps_url() -> None:
    job = Job.objects.create(
        source_type=SourceType.URL, source_url="https://youtube.com/watch?v=x"
    )
    assert job.source_url.endswith("?v=x")


# -------------------- Transcript --------------------


def test_transcript_one_per_job_enforced() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    Transcript.objects.create(
        job=job, language="en", full_text="hello", duration_sec=12.0
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Transcript.objects.create(
            job=job, language="en", full_text="again", duration_sec=13.0
        )


def test_transcript_default_whisper_model_and_segments_list() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    t = Transcript.objects.create(job=job, language="en", full_text="x", duration_sec=1)
    assert t.whisper_model == "whisper-1"
    assert t.segments_json == []


# -------------------- Analysis --------------------


def test_analysis_one_per_job_enforced() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    Analysis.objects.create(
        job=job,
        episode_title="T",
        hook="h",
        claude_model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Analysis.objects.create(
            job=job,
            episode_title="T2",
            hook="h2",
            claude_model="claude-sonnet-4-6",
            input_tokens=1,
            output_tokens=1,
        )


def test_analysis_json_defaults_are_containers() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    a = Analysis.objects.create(
        job=job,
        episode_title="T",
        hook="h",
        claude_model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
    )
    assert a.themes_json == []
    assert a.chapters_json == []
    assert a.clip_candidates_json == []
    assert a.quotes_json == []
    assert a.guest_json is None


# -------------------- Artifact --------------------


def test_artifact_defaults() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    art = Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0)
    assert art.status == ArtifactStatus.QUEUED
    assert art.version == 1
    assert art.metadata_json == {}


def test_artifact_unique_on_job_type_index() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0)
    # Same triple is rejected.
    with pytest.raises(IntegrityError), transaction.atomic():
        Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0)


def test_artifact_unique_allows_different_index() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0)
    # Different index on the same type is fine.
    Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=1)
    # Different type, same index is fine.
    Artifact.objects.create(job=job, type=ArtifactType.LINKEDIN_POST, index=0)
    assert Artifact.objects.filter(job=job).count() == 3


def test_artifact_manager_queries() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    ready = Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY
    )
    queued = Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=1, status=ArtifactStatus.QUEUED
    )
    processing = Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=2, status=ArtifactStatus.PROCESSING
    )
    failed = Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=3, status=ArtifactStatus.FAILED
    )

    assert list(Artifact.objects.ready_for_job(job.id)) == [ready]
    assert set(Artifact.objects.pending_for_job(job.id)) == {queued, processing}
    assert list(Artifact.objects.failed_for_job(job.id)) == [failed]


# -------------------- Cascade delete --------------------


def test_delete_job_cascades_all_children() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    Transcript.objects.create(job=job, language="en", full_text="x", duration_sec=1)
    Analysis.objects.create(
        job=job,
        episode_title="T",
        hook="h",
        claude_model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
    )
    Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0)

    job.delete()

    assert Job.objects.count() == 0
    assert Transcript.objects.count() == 0
    assert Analysis.objects.count() == 0
    assert Artifact.objects.count() == 0


# -------------------- Transitions --------------------


def test_all_non_terminal_states_can_fail() -> None:
    for state, allowed in JOB_TRANSITIONS.items():
        if state in (JobStatus.COMPLETED, JobStatus.FAILED):
            continue
        assert JobStatus.FAILED in allowed, f"{state} must be allowed to FAIL"


def test_happy_path_transitions() -> None:
    path = [
        JobStatus.PENDING,
        JobStatus.INGESTING,
        JobStatus.TRANSCRIBING,
        JobStatus.ANALYZING,
        JobStatus.GENERATING,
        JobStatus.PACKAGING,
        JobStatus.COMPLETED,
    ]
    for a, b in zip(path, path[1:]):
        assert can_transition(a, b), f"{a} → {b} should be legal"


def test_illegal_transitions_rejected() -> None:
    assert not can_transition(JobStatus.PENDING, JobStatus.COMPLETED)
    assert not can_transition(JobStatus.COMPLETED, JobStatus.FAILED)
    assert not can_transition(JobStatus.TRANSCRIBING, JobStatus.PENDING)
    assert not can_transition(JobStatus.FAILED, JobStatus.INGESTING)


def test_terminal_states_have_no_outgoing() -> None:
    assert JOB_TRANSITIONS[JobStatus.COMPLETED] == set()
    assert JOB_TRANSITIONS[JobStatus.FAILED] == set()
