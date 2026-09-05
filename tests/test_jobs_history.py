"""Pro feature 2 — job history list + delete."""
from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
    Transcript,
)
from services.jobs_service import delete_job, list_recent_jobs, summarize_job

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _job(status: str = JobStatus.COMPLETED, **kwargs) -> Job:
    return Job.objects.create(source_type=SourceType.FILE, status=status, **kwargs)


class TestListJobs:
    def test_newest_first_with_summary(self, client: APIClient) -> None:
        older = _job(original_filename="old.mp3")
        newer = _job(JobStatus.GENERATING, original_filename="new.mp3", duration_sec=600)
        # Both rows are created in the same millisecond — pin the order.
        Job.objects.filter(id=older.id).update(created_at="2026-01-01T00:00:00Z")
        Job.objects.filter(id=newer.id).update(created_at="2026-01-02T00:00:00Z")
        Analysis.objects.create(
            job=newer, episode_title="Fresh", hook="H", themes_json=[], chapters_json=[],
            clip_candidates_json=[], quotes_json=[], claude_model="m", input_tokens=1, output_tokens=1,
        )
        Artifact.objects.create(job=newer, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY)
        Artifact.objects.create(job=newer, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.FAILED)
        Artifact.objects.create(job=newer, type=ArtifactType.VIDEO_CLIP, index=1, status=ArtifactStatus.QUEUED)

        res = client.get("/api/jobs")
        assert res.status_code == 200
        rows = res.json()["jobs"]
        assert [r["job_id"] for r in rows] == [str(newer.id), str(older.id)]
        top = rows[0]
        assert top["episode_title"] == "Fresh"
        assert top["progress"] == {"total_artifacts": 3, "ready": 1, "failed": 1}
        assert top["duration_sec"] == 600
        assert top["has_package"] is False
        assert rows[1]["episode_title"] is None
        assert rows[1]["original_filename"] == "old.mp3"

    def test_limit_is_respected_and_clamped(self, client: APIClient) -> None:
        for _ in range(5):
            _job()
        assert len(client.get("/api/jobs?limit=2").json()["jobs"]) == 2
        assert len(client.get("/api/jobs?limit=abc").json()["jobs"]) == 5
        assert len(list_recent_jobs(limit=0)) == 1  # clamped to >= 1

    def test_empty(self, client: APIClient) -> None:
        assert client.get("/api/jobs").json() == {"jobs": []}

    def test_summary_serialises_timestamps(self) -> None:
        job = _job()
        row = summarize_job(Job.objects.select_related("analysis").prefetch_related("artifacts").get(id=job.id))
        assert row["created_at"].startswith(str(job.created_at.year))
        assert row["completed_at"] is None


class TestDeleteJob:
    def _job_with_files(self, media: Path) -> Job:
        job = _job()
        upload_dir = media / "uploads" / str(job.id)
        art_dir = media / "artifacts" / str(job.id)
        upload_dir.mkdir(parents=True)
        art_dir.mkdir(parents=True)
        (upload_dir / "raw.mp3").write_bytes(b"\x00" * 8)
        (art_dir / "clip_0_v1.mp4").write_bytes(b"\x00" * 8)
        zip_path = media / "packages" / "podcast_pack_x.zip"
        zip_path.parent.mkdir(parents=True)
        zip_path.write_bytes(b"PK")
        job.raw_media_path = str(upload_dir / "raw.mp3")
        job.package_path = "packages/podcast_pack_x.zip"
        job.save()
        Transcript.objects.create(job=job, language="en", full_text="x", segments_json=[], duration_sec=1)
        Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY)
        return job

    def test_delete_removes_row_children_and_files(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")):
            job = self._job_with_files(tmp_path)
            res = client.delete(f"/api/jobs/{job.id}")

        assert res.status_code == 200
        body = res.json()
        assert body["deleted"] is True
        assert body["removed_dirs"] == 2
        assert body["removed_files"] == 1
        assert not Job.objects.filter(id=job.id).exists()
        assert not Artifact.objects.filter(job_id=job.id).exists()
        assert not Transcript.objects.filter(job_id=job.id).exists()
        assert not (tmp_path / "uploads" / str(job.id)).exists()
        assert not (tmp_path / "artifacts" / str(job.id)).exists()
        assert not (tmp_path / "packages" / "podcast_pack_x.zip").exists()

    def test_delete_survives_missing_files(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")):
            job = _job(package_path="packages/gone.zip")
            counters = delete_job(job)
        assert counters == {"removed_dirs": 0, "removed_files": 0}
        assert not Job.objects.filter(id=job.id).exists()

    def test_delete_unknown_is_404(self, client: APIClient) -> None:
        res = client.delete("/api/jobs/00000000-0000-0000-0000-000000000000")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "JOB_NOT_FOUND"

    def test_get_still_works_on_same_route(self, client: APIClient) -> None:
        job = _job()
        assert client.get(f"/api/jobs/{job.id}").status_code == 200
