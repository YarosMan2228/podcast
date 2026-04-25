"""POST /api/artifacts/:id/regenerate (SPEC §5.3, §6.3).

Validates:
- 202 + correct payload on success
- 404 ArtifactNotFound for bad/missing artifact ids
- 400 InvalidTone for unknown tone values
- Correct task is dispatched per artifact type
- version incremented, status reset to QUEUED
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from jobs.models import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _make_job() -> Job:
    return Job.objects.create(source_type=SourceType.FILE)


def _make_artifact(job: Job, artifact_type: str, **kwargs) -> Artifact:
    defaults = {
        "index": 0,
        "status": ArtifactStatus.READY,
        "version": 1,
    }
    defaults.update(kwargs)
    return Artifact.objects.create(job=job, type=artifact_type, **defaults)


# ---------------------------------------------------------------------------
# 202 success — version bump + QUEUED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact_type,worker_module,task_name,queue",
    [
        (
            ArtifactType.LINKEDIN_POST,
            "workers.text_artifact_worker",
            "generate_linkedin_post",
            "text_artifacts",
        ),
        (
            ArtifactType.TWITTER_THREAD,
            "workers.text_artifact_worker",
            "generate_twitter_thread",
            "text_artifacts",
        ),
        (
            ArtifactType.SHOW_NOTES,
            "workers.text_artifact_worker",
            "generate_show_notes",
            "text_artifacts",
        ),
        (
            ArtifactType.NEWSLETTER,
            "workers.text_artifact_worker",
            "generate_newsletter",
            "text_artifacts",
        ),
        (
            ArtifactType.YOUTUBE_DESCRIPTION,
            "workers.text_artifact_worker",
            "generate_youtube_description",
            "text_artifacts",
        ),
        (
            ArtifactType.QUOTE_GRAPHIC,
            "workers.quote_graphic_worker",
            "generate_quote_graphic",
            "graphics",
        ),
    ],
)
def test_regenerate_dispatches_correct_task(
    client: APIClient,
    artifact_type: str,
    worker_module: str,
    task_name: str,
    queue: str,
) -> None:
    job = _make_job()
    artifact = _make_artifact(job, artifact_type)

    with patch(f"{worker_module}.{task_name}") as mock_task:
        mock_task.apply_async = lambda *a, **kw: None
        res = client.post(f"/api/artifacts/{artifact.id}/regenerate")

    assert res.status_code == 202
    body = res.json()
    assert body["artifact_id"] == str(artifact.id)
    assert body["status"] == ArtifactStatus.QUEUED
    assert body["version"] == 2

    artifact.refresh_from_db()
    assert artifact.version == 2
    assert artifact.status == ArtifactStatus.QUEUED
    assert artifact.error is None


def test_regenerate_video_clip_dispatches_to_video_queue(client: APIClient) -> None:
    job = _make_job()
    artifact = _make_artifact(job, ArtifactType.VIDEO_CLIP)

    dispatched: list[dict] = []

    def fake_apply_async(args, queue):
        dispatched.append({"args": args, "queue": queue})

    with patch("workers.video_clip_worker.generate_video_clip") as mock_task:
        mock_task.apply_async = fake_apply_async
        res = client.post(f"/api/artifacts/{artifact.id}/regenerate")

    assert res.status_code == 202
    assert res.json()["version"] == 2
    assert len(dispatched) == 1
    assert dispatched[0]["args"] == [str(artifact.id), True]
    assert dispatched[0]["queue"] == "video"


def test_regenerate_version_increments_from_current(client: APIClient) -> None:
    job = _make_job()
    artifact = _make_artifact(job, ArtifactType.LINKEDIN_POST, version=4)

    with patch("workers.text_artifact_worker.generate_linkedin_post") as mock_task:
        mock_task.apply_async = lambda *a, **kw: None
        res = client.post(f"/api/artifacts/{artifact.id}/regenerate")

    assert res.status_code == 202
    assert res.json()["version"] == 5
    artifact.refresh_from_db()
    assert artifact.version == 5


def test_regenerate_clears_error_field(client: APIClient) -> None:
    job = _make_job()
    artifact = _make_artifact(
        job, ArtifactType.LINKEDIN_POST, status=ArtifactStatus.FAILED, error="old error"
    )

    with patch("workers.text_artifact_worker.generate_linkedin_post") as mock_task:
        mock_task.apply_async = lambda *a, **kw: None
        client.post(f"/api/artifacts/{artifact.id}/regenerate")

    artifact.refresh_from_db()
    assert artifact.error is None
    assert artifact.status == ArtifactStatus.QUEUED


# ---------------------------------------------------------------------------
# Tone support for text artifacts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tone", ["analytical", "casual", "punchy", "professional"])
def test_regenerate_text_artifact_with_valid_tone(client: APIClient, tone: str) -> None:
    job = _make_job()
    artifact = _make_artifact(job, ArtifactType.LINKEDIN_POST)

    dispatched: list[dict] = []

    def fake_apply_async(args, queue):
        dispatched.append({"args": args, "queue": queue})

    with patch("workers.text_artifact_worker.generate_linkedin_post") as mock_task:
        mock_task.apply_async = fake_apply_async
        res = client.post(
            f"/api/artifacts/{artifact.id}/regenerate",
            data={"tone": tone},
            format="json",
        )

    assert res.status_code == 202
    assert dispatched[0]["args"] == [str(artifact.id), tone]
    assert dispatched[0]["queue"] == "text_artifacts"


def test_regenerate_no_tone_omits_from_args(client: APIClient) -> None:
    job = _make_job()
    artifact = _make_artifact(job, ArtifactType.LINKEDIN_POST)

    dispatched: list[dict] = []

    def fake_apply_async(args, queue):
        dispatched.append({"args": args, "queue": queue})

    with patch("workers.text_artifact_worker.generate_linkedin_post") as mock_task:
        mock_task.apply_async = fake_apply_async
        res = client.post(f"/api/artifacts/{artifact.id}/regenerate")

    assert res.status_code == 202
    assert dispatched[0]["args"] == [str(artifact.id)]


# ---------------------------------------------------------------------------
# 400 InvalidTone
# ---------------------------------------------------------------------------


def test_regenerate_invalid_tone_returns_400(client: APIClient) -> None:
    job = _make_job()
    artifact = _make_artifact(job, ArtifactType.LINKEDIN_POST)

    res = client.post(
        f"/api/artifacts/{artifact.id}/regenerate",
        data={"tone": "snarky"},
        format="json",
    )

    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "INVALID_TONE"
    assert body["error"]["field"] == "tone"
    assert "snarky" in body["error"]["message"]


# ---------------------------------------------------------------------------
# 404 ArtifactNotFound
# ---------------------------------------------------------------------------


def test_regenerate_unknown_artifact_returns_404(client: APIClient) -> None:
    res = client.post(f"/api/artifacts/{uuid.uuid4()}/regenerate")
    assert res.status_code == 404
    body = res.json()
    assert body["error"]["code"] == "ARTIFACT_NOT_FOUND"
    assert body["error"]["field"] == "artifact_id"


def test_regenerate_invalid_uuid_returns_404(client: APIClient) -> None:
    res = client.post("/api/artifacts/not-a-uuid/regenerate")
    assert res.status_code == 404
    body = res.json()
    assert body["error"]["code"] == "ARTIFACT_NOT_FOUND"
