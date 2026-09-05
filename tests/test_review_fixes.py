"""Regression tests for the post-Day-7 review batch.

Each class maps to one fix — see STATUS.md "Review batch" for the story:

* media serving under ``/media/`` (previews + ZIP link were 404)
* regenerate: single version bump, SPEC §6.5 rate limit (429 + Retry-After)
* re-packaging after regenerate on a COMPLETED job
* terminal catch-alls: pipeline tasks + video worker never leave a Job /
  Artifact stuck in a non-terminal state on unexpected exceptions
* text / quote workers: permanent errors fail fast, no 180s retry delay
* orchestrator creates only as many QUOTE_GRAPHIC rows as eligible quotes
* upload accepts ``application/octet-stream`` when the extension is media
* tweet splitter handles tweets several times over the limit
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
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
from services.claude_client import ClaudeError
from workers.packager import package_job
from workers.tasks import (
    analyze_job_task,
    check_and_trigger_packaging,
    orchestrate_artifacts,
    start_job,
    transcribe_job_task,
)
from workers.text_artifact_worker import (
    _split_tweet_at_limit,
    generate_linkedin_post,
    is_permanent_error,
    retry_countdown_sec,
)
from workers.video_clip_worker import generate_video_clip

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _quote(text: str) -> dict:
    return {"text": text, "speaker": "S", "ts_ms": 0}


def _make_full_job(tmp_path: Path, *, quotes: list[dict] | None = None) -> Job:
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 16)
    job = Job.objects.create(
        source_type=SourceType.FILE,
        status=JobStatus.GENERATING,
        raw_media_path=str(raw),
        duration_sec=120.0,
        mime_type="video/mp4",
    )
    Transcript.objects.create(
        job=job,
        language="en",
        full_text="hello world",
        segments_json=[
            {
                "start_ms": 10_000,
                "end_ms": 40_000,
                "words": [{"w": "hello", "start_ms": 11_000, "end_ms": 11_500}],
            }
        ],
        duration_sec=120.0,
    )
    Analysis.objects.create(
        job=job,
        episode_title="t",
        hook="h",
        themes_json=[],
        chapters_json=[],
        clip_candidates_json=[
            {"start_ms": 10_000, "end_ms": 40_000, "virality_score": 9, "reason": "r", "hook_text": "a"},
            {"start_ms": 50_000, "end_ms": 85_000, "virality_score": 7, "reason": "r", "hook_text": "b"},
        ],
        quotes_json=quotes if quotes is not None else [],
        claude_model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
    )
    return job


def _ffmpeg_stub(*, output_path: str, **_kwargs) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"\x00" * 8192)


# ---------------------------------------------------------------------------
# /media/ serving
# ---------------------------------------------------------------------------


class TestMediaServing:
    def test_media_files_are_served(self, client: APIClient, tmp_path: Path) -> None:
        f = tmp_path / "artifacts" / "j" / "quote_0.png"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            res = client.get("/media/artifacts/j/quote_0.png")
        assert res.status_code == 200
        assert b"".join(res.streaming_content).startswith(b"\x89PNG")

    def test_missing_media_is_404(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            res = client.get("/media/packages/nope.zip")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# regenerate — version + rate limit
# ---------------------------------------------------------------------------


class TestRegenerateVersion:
    def test_endpoint_and_worker_agree_on_version(
        self, client: APIClient, tmp_path: Path
    ) -> None:
        """One regenerate → version 2 in the response, v2 on disk (was v3)."""
        job = _make_full_job(tmp_path)
        artifact = Artifact.objects.create(
            job=job,
            type=ArtifactType.VIDEO_CLIP,
            index=0,
            status=ArtifactStatus.READY,
            file_path=f"artifacts/{job.id}/clip_0_v1.mp4",
            metadata_json={"used_candidate_indices": [0]},
        )
        media = tmp_path / "media"
        with override_settings(
            MEDIA_ROOT=str(media), ARTIFACTS_ROOT=str(media / "artifacts")
        ), patch(
            "workers.video_clip_worker.build_vertical_clip", side_effect=_ffmpeg_stub
        ), patch("workers.video_clip_worker.publish"):
            # Eager Celery: the worker runs inline inside the request.
            res = client.post(f"/api/artifacts/{artifact.id}/regenerate", {}, format="json")

        assert res.status_code == 202
        assert res.json()["version"] == 2
        artifact.refresh_from_db()
        assert artifact.version == 2
        assert artifact.file_path.endswith("clip_0_v2.mp4")
        assert artifact.metadata_json["source_clip_candidate_index"] == 1


class TestRegenerateRateLimit:
    def _artifact(self) -> Artifact:
        job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.COMPLETED)
        return Artifact.objects.create(
            job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY
        )

    def test_fourth_call_within_a_minute_is_429_with_retry_after(
        self, client: APIClient
    ) -> None:
        artifact = self._artifact()
        url = f"/api/artifacts/{artifact.id}/regenerate"
        with patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"):
            for _ in range(3):
                assert client.post(url, {}, format="json").status_code == 202
            res = client.post(url, {}, format="json")

        assert res.status_code == 429
        body = res.json()["error"]
        assert body["code"] == "REGENERATE_RATE_LIMITED"
        assert 1 <= int(res["Retry-After"]) <= 60
        # The rejected call must not have touched the row.
        artifact.refresh_from_db()
        assert artifact.version == 4

    def test_limit_is_per_artifact(self, client: APIClient) -> None:
        a, b = self._artifact(), self._artifact()
        with patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"):
            for _ in range(3):
                client.post(f"/api/artifacts/{a.id}/regenerate", {}, format="json")
            res = client.post(f"/api/artifacts/{b.id}/regenerate", {}, format="json")
        assert res.status_code == 202

    def test_limit_can_be_disabled(self, client: APIClient) -> None:
        artifact = self._artifact()
        with override_settings(REGENERATE_LIMIT_PER_MINUTE=0), patch(
            "workers.text_artifact_worker.generate_linkedin_post.apply_async"
        ):
            for _ in range(5):
                res = client.post(f"/api/artifacts/{artifact.id}/regenerate", {}, format="json")
                assert res.status_code == 202


# ---------------------------------------------------------------------------
# re-packaging after regenerate on a COMPLETED job
# ---------------------------------------------------------------------------


class TestRepackage:
    def _completed_job(self, media: Path) -> tuple[Job, Path]:
        job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.COMPLETED)
        old_zip = media / "packages" / "podcast_pack_old.zip"
        old_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(old_zip, "w") as zf:
            zf.writestr("text/linkedin.md", "old")
        job.package_path = "packages/podcast_pack_old.zip"
        job.save()
        Artifact.objects.create(
            job=job,
            type=ArtifactType.LINKEDIN_POST,
            index=0,
            status=ArtifactStatus.READY,
            text_content="NEW CONTENT",
            version=2,
        )
        return job, old_zip

    def test_trigger_dispatches_repackage_for_completed_job(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job, _ = self._completed_job(tmp_path)
            with patch("workers.packager.package_job.apply_async") as enq:
                assert check_and_trigger_packaging(str(job.id)) is True
        enq.assert_called_once()
        assert enq.call_args.kwargs.get("kwargs") == {"repackage": True}

    def test_trigger_waits_for_pending_artifact_on_completed_job(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job, _ = self._completed_job(tmp_path)
            Artifact.objects.create(
                job=job, type=ArtifactType.TWITTER_THREAD, index=0, status=ArtifactStatus.QUEUED
            )
            with patch("workers.packager.package_job.apply_async") as enq:
                assert check_and_trigger_packaging(str(job.id)) is False
        enq.assert_not_called()

    def test_repackage_rebuilds_zip_and_swaps_package_path(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path), MEDIA_URL="/media/"):
            job, old_zip = self._completed_job(tmp_path)
            with patch("workers.packager.publish") as publish_mock:
                package_job.apply(args=[str(job.id)], kwargs={"repackage": True})

            job.refresh_from_db()
            assert job.status == JobStatus.COMPLETED
            assert job.package_path != "packages/podcast_pack_old.zip"
            new_zip = tmp_path / job.package_path
            assert new_zip.exists()
            with zipfile.ZipFile(new_zip) as zf:
                assert zf.read("text/linkedin.md").decode() == "NEW CONTENT"
            assert not old_zip.exists(), "superseded archive should be removed"

        publish_mock.assert_called_once()
        event, payload = publish_mock.call_args.args[1], publish_mock.call_args.args[2]
        assert event == "completed"
        assert payload["package_url"] == "/media/" + job.package_path

    def test_repackage_is_noop_for_non_completed_job(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.GENERATING)
            with patch("workers.packager.publish") as publish_mock:
                package_job.apply(args=[str(job.id)], kwargs={"repackage": True})
        publish_mock.assert_not_called()
        job.refresh_from_db()
        assert job.status == JobStatus.GENERATING


# ---------------------------------------------------------------------------
# terminal catch-alls
# ---------------------------------------------------------------------------


class TestPipelineUnexpectedErrors:
    @pytest.mark.parametrize(
        "task,start_status,patch_target,code",
        [
            (start_job, JobStatus.PENDING, "workers.tasks.ingest_job", "INGESTION_ERROR"),
            (transcribe_job_task, JobStatus.INGESTING, "workers.tasks.transcribe_job", "TRANSCRIPTION_ERROR"),
            (analyze_job_task, JobStatus.TRANSCRIBING, "workers.tasks.analyze_job", "ANALYSIS_ERROR"),
        ],
    )
    def test_unexpected_exception_fails_job_instead_of_hanging(
        self, task, start_status, patch_target, code
    ) -> None:
        job = Job.objects.create(source_type=SourceType.FILE, status=start_status)
        with patch(patch_target, side_effect=RuntimeError("db went away")):
            task.apply_async(args=[str(job.id)])
        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert code in job.error
        assert "db went away" in job.error
        assert job.completed_at is not None

    def test_orchestrate_unexpected_exception_drains_queued_artifacts(self, tmp_path: Path) -> None:
        job = _make_full_job(tmp_path)
        job.status = JobStatus.ANALYZING
        job.save()
        # Video dispatch blows up (broker down) after rows were created.
        with patch(
            "workers.video_clip_worker.generate_video_clip.apply_async",
            side_effect=ConnectionError("broker down"),
        ):
            orchestrate_artifacts.apply_async(args=[str(job.id)])
        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "ORCHESTRATE_ERROR" in job.error
        assert not Artifact.objects.filter(job=job, status=ArtifactStatus.QUEUED).exists()


class TestVideoWorkerCatchAll:
    def test_unexpected_exception_marks_artifact_failed(self, tmp_path: Path) -> None:
        job = _make_full_job(tmp_path)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.QUEUED
        )
        with patch(
            "workers.video_clip_worker.build_ass", side_effect=KeyError("start_ms")
        ), patch("workers.video_clip_worker.publish"):
            generate_video_clip.apply_async(args=[str(artifact.id)])
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "CLIP_INTERNAL_ERROR" in artifact.error


# ---------------------------------------------------------------------------
# text / quote worker retry policy
# ---------------------------------------------------------------------------


class TestWorkerRetryPolicy:
    def test_permanent_error_classification(self) -> None:
        assert is_permanent_error(ClaudeError("401", transient=False))
        assert not is_permanent_error(ClaudeError("503", transient=True))
        assert is_permanent_error(ValueError("bad json"))
        assert is_permanent_error(KeyError("tweets"))
        assert is_permanent_error(Artifact.DoesNotExist())
        assert not is_permanent_error(RuntimeError("playwright flake"))

    def test_backoff_is_short(self) -> None:
        assert [retry_countdown_sec(i) for i in range(3)] == [1, 2, 4]

    def test_permanent_claude_error_fails_without_retry(self, tmp_path: Path) -> None:
        """max_retries is left at 3 — a non-transient ClaudeError must still
        land in FAILED on the first attempt (before: 3 × 180s of retries)."""
        job = _make_full_job(tmp_path)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.QUEUED
        )
        with patch(
            "workers.text_artifact_worker.call_text_artifact",
            side_effect=ClaudeError("invalid x-api-key", transient=False),
        ), patch("workers.text_artifact_worker.publish"):
            generate_linkedin_post.apply_async(args=[str(artifact.id)])
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "x-api-key" in artifact.error


# ---------------------------------------------------------------------------
# orchestrator — quote graphic slots follow eligible quote count
# ---------------------------------------------------------------------------


class TestOrchestratorQuoteSlots:
    def _run(self, job: Job) -> None:
        with (
            patch("workers.video_clip_worker.generate_video_clip.apply_async"),
            patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"),
            patch("workers.text_artifact_worker.generate_twitter_thread.apply_async"),
            patch("workers.text_artifact_worker.generate_show_notes.apply_async"),
            patch("workers.text_artifact_worker.generate_newsletter.apply_async"),
            patch("workers.text_artifact_worker.generate_youtube_description.apply_async"),
            patch("workers.quote_graphic_worker.generate_quote_graphic.apply_async"),
        ):
            orchestrate_artifacts.apply_async(args=[str(job.id)])

    def test_two_eligible_quotes_create_two_slots(self, tmp_path: Path) -> None:
        job = _make_full_job(
            tmp_path,
            quotes=[_quote("x" * 40), _quote("y" * 60), _quote("short"), _quote("z" * 500)],
        )
        job.status = JobStatus.ANALYZING
        job.save()
        self._run(job)
        assert Artifact.objects.filter(job=job, type=ArtifactType.QUOTE_GRAPHIC).count() == 2

    def test_no_eligible_quotes_create_no_slots(self, tmp_path: Path) -> None:
        job = _make_full_job(tmp_path, quotes=[_quote("short")])
        job.status = JobStatus.ANALYZING
        job.save()
        self._run(job)
        assert not Artifact.objects.filter(job=job, type=ArtifactType.QUOTE_GRAPHIC).exists()
        job.refresh_from_db()
        assert job.status == JobStatus.GENERATING

    def test_many_quotes_are_capped_at_five(self, tmp_path: Path) -> None:
        job = _make_full_job(tmp_path, quotes=[_quote(f"quote number {i} " * 3) for i in range(9)])
        job.status = JobStatus.ANALYZING
        job.save()
        self._run(job)
        assert Artifact.objects.filter(job=job, type=ArtifactType.QUOTE_GRAPHIC).count() == 5


# ---------------------------------------------------------------------------
# upload MIME fallback
# ---------------------------------------------------------------------------


class TestUploadMimeFallback:
    def test_octet_stream_with_media_extension_is_accepted(
        self, client: APIClient, tmp_path: Path
    ) -> None:
        f = SimpleUploadedFile("ep.m4a", b"\x00" * 64, content_type="application/octet-stream")
        with override_settings(MEDIA_ROOT=str(tmp_path)), patch(
            "api.views.upload.start_job.apply_async"
        ):
            res = client.post("/api/jobs/upload", {"file": f}, format="multipart")
        assert res.status_code == 201, res.content
        job = Job.objects.get(id=res.json()["job_id"])
        assert job.mime_type.startswith("audio/")

    def test_octet_stream_with_non_media_extension_is_rejected(
        self, client: APIClient, tmp_path: Path
    ) -> None:
        f = SimpleUploadedFile("doc.pdf", b"\x00" * 64, content_type="application/octet-stream")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            res = client.post("/api/jobs/upload", {"file": f}, format="multipart")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "UPLOAD_INVALID_FORMAT"
        assert Job.objects.count() == 0


# ---------------------------------------------------------------------------
# tweet splitter
# ---------------------------------------------------------------------------


def test_split_tweet_handles_multiples_of_the_limit() -> None:
    tweet = " ".join(["word"] * 200)  # ~1000 chars
    parts = _split_tweet_at_limit(tweet, limit=270)
    assert len(parts) >= 4
    assert all(len(p) <= 270 for p in parts)
    assert " ".join(parts) == tweet


def test_split_tweet_keeps_short_tweet_intact() -> None:
    assert _split_tweet_at_limit("hello", limit=270) == ["hello"]
