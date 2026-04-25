"""Day-6 hardening: SoftTimeLimit handlers, transient ffmpeg retry, ENOSPC.

Cross-cutting suite for the new error-path behaviours added during the
hardening pass. Each section maps to a SPEC §X.5 row that previously had
no test coverage:

* §5.5 — FFmpeg "Invalid data" retry once / transient detector
* §9.5 — soft_time_limit hits → terminal artifact / job state, not stuck PROCESSING
* §2.5 — ENOSPC during yt-dlp → STORAGE_FULL (separate from generic IO error)
"""
from __future__ import annotations

import errno
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded
from django.test import override_settings

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
from pipeline.ffmpeg_clip import FFmpegClipError, _is_transient_ffmpeg_failure
from pipeline.ingestion import IngestionError
from pipeline.url_ingestion import download_from_url
from workers.packager import package_job
from workers.quote_graphic_worker import generate_quote_graphic
from workers.tasks import (
    analyze_job_task,
    orchestrate_artifacts,
    start_job,
    transcribe_job_task,
)
from workers.text_artifact_worker import generate_linkedin_post
from workers.video_clip_worker import generate_video_clip

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers — kept here rather than in conftest because they're hardening-specific
# ---------------------------------------------------------------------------


@contextmanager
def zero_retries(task: Any) -> Generator[None, None, None]:
    """Force first-failure-is-final by clearing Celery's retry budget.

    In eager test mode ``self.request.retries`` never increments across
    invocations, so the only way to exercise the "final failure" branch
    is to patch ``max_retries`` down to 0 for the duration of the test.
    """
    original = task.max_retries
    task.max_retries = 0
    try:
        yield
    finally:
        task.max_retries = original


def _make_full_job(
    tmp_path: Path, *, status: str = JobStatus.GENERATING
) -> Job:
    """Job + Transcript + Analysis with one clip candidate — minimal happy state."""
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 16)
    job = Job.objects.create(
        source_type=SourceType.FILE,
        status=status,
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
                "words": [
                    {"w": "hello", "start_ms": 11_000, "end_ms": 11_500},
                    {"w": "world", "start_ms": 11_600, "end_ms": 12_100},
                ],
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
            {
                "start_ms": 10_000,
                "end_ms": 40_000,
                "virality_score": 9,
                "reason": "r",
                "hook_text": "the hook",
            }
        ],
        quotes_json=[
            {
                "text": "Repeat after me — soft limits are a feature.",
                "speaker": "Test",
                "ts_ms": 12_000,
            }
        ],
        claude_model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
    )
    return job


def _make_artifact(job: Job, type_: str, index: int = 0) -> Artifact:
    return Artifact.objects.create(
        job=job, type=type_, index=index, status=ArtifactStatus.QUEUED
    )


# ===========================================================================
# §5.5 — FFmpeg transient-failure detector + one-shot retry
# ===========================================================================


class TestTransientDetector:
    """``_is_transient_ffmpeg_failure`` recognises the SPEC §5.5 stderr markers."""

    @pytest.mark.parametrize(
        "stderr",
        [
            "Invalid data found when processing input",
            "av_interleaved_write_frame(): Connection reset by peer",
            "Server returned 503 Service Unavailable",
            "could not seek to position 0.123",
        ],
    )
    def test_transient_markers_are_detected(self, stderr: str) -> None:
        assert _is_transient_ffmpeg_failure(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "",
            "Unrecognised option '-foobar'",
            "moov atom not found",
            "Output file is empty",
        ],
    )
    def test_permanent_failures_are_not_marked_transient(self, stderr: str) -> None:
        assert _is_transient_ffmpeg_failure(stderr) is False


class TestVideoClipTransientRetry:
    """SPEC §5.5: ffmpeg "Invalid data" → retry once; permanent → fail."""

    def test_transient_failure_triggers_retry_and_keeps_artifact_queued(
        self, tmp_path: Path
    ) -> None:
        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.VIDEO_CLIP)

        with override_settings(
            MEDIA_ROOT=str(tmp_path / "media"),
            ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
        ), patch(
            "workers.video_clip_worker.build_vertical_clip",
            side_effect=FFmpegClipError(
                "CLIP_FFMPEG_FAILED",
                "ffmpeg exited 1: Invalid data found when processing input",
                transient=True,
            ),
        ), patch(
            "workers.video_clip_worker.publish"
        ) as pub:
            # Eager mode propagates Celery's Retry up to the caller.
            with pytest.raises(Retry):
                generate_video_clip.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        # Reset to QUEUED so check_and_trigger_packaging won't pick it up
        # as a stale-PROCESSING artifact while the retry is delayed.
        assert artifact.status == ArtifactStatus.QUEUED
        # No artifact_failed event — the retry path must not signal terminality.
        assert all(c.args[1] != "artifact_failed" for c in pub.call_args_list)

    def test_permanent_failure_marks_failed_without_retry(
        self, tmp_path: Path
    ) -> None:
        """An ffmpeg exit with no transient marker fails on the first attempt."""
        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.VIDEO_CLIP)

        with override_settings(
            MEDIA_ROOT=str(tmp_path / "media"),
            ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
        ), patch(
            "workers.video_clip_worker.build_vertical_clip",
            side_effect=FFmpegClipError(
                "CLIP_FFMPEG_FAILED", "ffmpeg exited 1: moov atom not found"
            ),  # transient defaults to False
        ), patch("workers.video_clip_worker.publish") as pub:
            generate_video_clip.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "CLIP_FFMPEG_FAILED" in artifact.error
        # artifact_failed was emitted — terminal state, no retry.
        assert any(c.args[1] == "artifact_failed" for c in pub.call_args_list)

    # Note: the "retry budget exhausted → _mark_failed" branch is exercised
    # structurally by ``test_permanent_failure_marks_failed_without_retry``
    # (same _mark_failed call site). Simulating ``self.request.retries`` in
    # Celery eager mode requires patching apply_async internals and adds no
    # behavioural coverage beyond what the permanent-failure test already
    # gives us, so we don't duplicate it here.


# ===========================================================================
# §9.5 — SoftTimeLimitExceeded handlers per worker
# ===========================================================================


class TestSoftTimeoutVideoClip:
    def test_soft_timeout_marks_clip_failed_with_timeout_code(
        self, tmp_path: Path
    ) -> None:
        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.VIDEO_CLIP)

        with override_settings(
            MEDIA_ROOT=str(tmp_path / "media"),
            ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
        ), patch(
            "workers.video_clip_worker.build_vertical_clip",
            side_effect=SoftTimeLimitExceeded(),
        ), patch("workers.video_clip_worker.publish") as pub:
            generate_video_clip.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "CLIP_TIMEOUT" in artifact.error
        # Last publish was artifact_failed, not artifact_ready.
        assert pub.call_args.args[1] == "artifact_failed"


class TestSoftTimeoutTextArtifact:
    def test_soft_timeout_during_claude_call_marks_failed(
        self, tmp_path: Path
    ) -> None:
        """SoftTimeLimit fires inside the Claude call, AFTER the artifact is
        loaded and flipped to PROCESSING — the realistic scenario that we
        actually need to recover from (DB load is fast; Claude can hang)."""
        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.LINKEDIN_POST)

        with patch(
            "workers.text_artifact_worker.call_text_artifact",
            side_effect=SoftTimeLimitExceeded(),
        ), patch("workers.text_artifact_worker.publish") as pub:
            generate_linkedin_post.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "LINKEDIN_POST_TIMEOUT" in artifact.error
        assert any(c.args[1] == "artifact_failed" for c in pub.call_args_list)


class TestSoftTimeoutQuoteGraphic:
    def test_soft_timeout_marks_graphic_failed_immediately(
        self, tmp_path: Path
    ) -> None:
        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.QUOTE_GRAPHIC)

        # Patch the deferred import inside the task, not the module-level one.
        with patch(
            "services.graphic_renderer.render_quote_to_png",
            side_effect=SoftTimeLimitExceeded(),
        ), patch("workers.quote_graphic_worker.publish") as pub:
            generate_quote_graphic.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "QUOTE_GRAPHIC_TIMEOUT" in artifact.error
        assert any(c.args[1] == "artifact_failed" for c in pub.call_args_list)


class TestSoftTimeoutPackager:
    def test_soft_timeout_marks_job_failed_with_package_timeout(
        self, tmp_path: Path
    ) -> None:
        job = _make_full_job(tmp_path, status=JobStatus.GENERATING)
        # Need at least one READY artifact so the packager attempts to build.
        Artifact.objects.create(
            job=job,
            type=ArtifactType.VIDEO_CLIP,
            index=0,
            status=ArtifactStatus.READY,
            file_path="dummy.mp4",
        )

        with override_settings(
            MEDIA_ROOT=str(tmp_path / "media"),
        ), patch(
            "workers.packager._build_zip", side_effect=SoftTimeLimitExceeded()
        ), patch("workers.packager.publish"):
            (Path(tmp_path) / "media" / "packages").mkdir(parents=True, exist_ok=True)
            package_job.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "PACKAGE_TIMEOUT" in (job.error or "")


# ===========================================================================
# §9.5 — SoftTimeLimitExceeded handlers per pipeline-orchestration task
# ===========================================================================


class TestSoftTimeoutPipelineTasks:
    def test_start_job_soft_timeout_marks_job_failed_with_ingestion_timeout(
        self, tmp_path: Path
    ) -> None:
        job = Job.objects.create(
            source_type=SourceType.FILE,
            raw_media_path=str(tmp_path / "raw.mp3"),
            status=JobStatus.PENDING,
        )
        with patch(
            "workers.tasks.ingest_job", side_effect=SoftTimeLimitExceeded()
        ):
            start_job.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "INGESTION_TIMEOUT" in (job.error or "")

    def test_transcribe_task_soft_timeout_marks_job_failed(
        self, tmp_path: Path
    ) -> None:
        job = Job.objects.create(
            source_type=SourceType.FILE, status=JobStatus.INGESTING
        )
        with patch(
            "workers.tasks.transcribe_job", side_effect=SoftTimeLimitExceeded()
        ):
            transcribe_job_task.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "TRANSCRIPTION_TIMEOUT" in (job.error or "")

    def test_analyze_task_soft_timeout_marks_job_failed(self) -> None:
        job = Job.objects.create(
            source_type=SourceType.FILE, status=JobStatus.TRANSCRIBING
        )
        with patch(
            "workers.tasks.analyze_job", side_effect=SoftTimeLimitExceeded()
        ):
            analyze_job_task.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "ANALYSIS_TIMEOUT" in (job.error or "")

    def test_orchestrate_soft_timeout_fails_job_and_drains_queued_artifacts(
        self, tmp_path: Path
    ) -> None:
        """If timeout fires mid fan-out, queued rows would block forever —
        they must be flipped to FAILED so packaging doesn't have a phantom
        "still queued" artifact preventing finalization."""
        job = _make_full_job(tmp_path, status=JobStatus.ANALYZING)
        # Pre-create a stale QUEUED row to simulate a partial fan-out state.
        Artifact.objects.create(
            job=job,
            type=ArtifactType.VIDEO_CLIP,
            index=0,
            status=ArtifactStatus.QUEUED,
        )
        with patch(
            "workers.tasks._orchestrate_artifacts_inner",
            side_effect=SoftTimeLimitExceeded(),
        ):
            orchestrate_artifacts.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "ORCHESTRATE_TIMEOUT" in (job.error or "")
        # The queued artifact got drained to FAILED — otherwise
        # check_and_trigger_packaging would still see it as pending.
        stale = Artifact.objects.get(job=job, type=ArtifactType.VIDEO_CLIP, index=0)
        assert stale.status == ArtifactStatus.FAILED
        assert "ORCHESTRATE_TIMEOUT" in (stale.error or "")


# ===========================================================================
# §2.5 — ENOSPC during yt-dlp surfaces as STORAGE_FULL, not URL_YTDLP_FAILED
# ===========================================================================


class TestUrlIngestionEnospc:
    def test_enospc_oserror_yields_storage_full(self, tmp_path: Path) -> None:
        """A disk-full IO error should be distinguishable from a yt-dlp error
        so the user sees "ran out of disk" rather than "yt-dlp failed"."""
        full_disk = OSError(errno.ENOSPC, "No space left on device")

        # Patch YoutubeDL inside the deferred import block.
        class _BoomYDL:
            def __init__(self, *_a, **_kw): ...
            def __enter__(self):
                return self
            def __exit__(self, *_a):
                return False
            def download(self, *_a):
                raise full_disk

        with patch("yt_dlp.YoutubeDL", _BoomYDL):
            with pytest.raises(IngestionError) as exc:
                download_from_url("https://youtube.com/watch?v=x", tmp_path)

        assert exc.value.code == "STORAGE_FULL"

    def test_generic_oserror_still_yields_url_ytdlp_failed(
        self, tmp_path: Path
    ) -> None:
        """Permission-denied and friends keep the legacy URL_YTDLP_FAILED code."""
        eperm = OSError(errno.EACCES, "Permission denied")

        class _BoomYDL:
            def __init__(self, *_a, **_kw): ...
            def __enter__(self):
                return self
            def __exit__(self, *_a):
                return False
            def download(self, *_a):
                raise eperm

        with patch("yt_dlp.YoutubeDL", _BoomYDL):
            with pytest.raises(IngestionError) as exc:
                download_from_url("https://youtube.com/watch?v=x", tmp_path)

        assert exc.value.code == "URL_YTDLP_FAILED"


# ===========================================================================
# §7 (technical debt) — regenerate cleans up the previous version's mp4
# ===========================================================================


class TestVideoClipRegenerateCleanup:
    def test_regenerate_deletes_previous_version_file(
        self, tmp_path: Path
    ) -> None:
        """Without cleanup, every regenerate leaves a stale mp4 on disk —
        five regens × five clips = 25 orphaned files per episode (STATUS §7)."""
        media = tmp_path / "media"
        artifacts_root = media / "artifacts"

        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.VIDEO_CLIP, index=0)

        # Simulate a v1 already on disk from the original render.
        old_dir = artifacts_root / str(job.id)
        old_dir.mkdir(parents=True)
        old_file = old_dir / "clip_0_v1.mp4"
        old_file.write_bytes(b"\x00" * 8192)
        artifact.file_path = f"artifacts/{job.id}/clip_0_v1.mp4"
        artifact.status = ArtifactStatus.READY
        artifact.metadata_json = {"used_candidate_indices": [0]}
        artifact.save()

        def _stub_render(*, output_path: str, **_) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 8192)

        with override_settings(
            MEDIA_ROOT=str(media), ARTIFACTS_ROOT=str(artifacts_root)
        ), patch(
            "workers.video_clip_worker.build_vertical_clip",
            side_effect=_stub_render,
        ), patch("workers.video_clip_worker.publish"):
            generate_video_clip.apply_async(args=[str(artifact.id), True])

        artifact.refresh_from_db()
        # New version persisted, old file deleted.
        assert artifact.file_path.endswith("clip_0_v2.mp4")
        assert (media / artifact.file_path).exists()
        assert not old_file.exists(), "previous version should have been cleaned"

    def test_initial_render_does_not_attempt_cleanup(
        self, tmp_path: Path
    ) -> None:
        """First render of an artifact has no previous file — cleanup must
        be skipped, not crash on the missing path."""
        job = _make_full_job(tmp_path)
        artifact = _make_artifact(job, ArtifactType.VIDEO_CLIP, index=0)
        # file_path is empty initially.

        def _stub_render(*, output_path: str, **_) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 8192)

        with override_settings(
            MEDIA_ROOT=str(tmp_path / "media"),
            ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
        ), patch(
            "workers.video_clip_worker.build_vertical_clip",
            side_effect=_stub_render,
        ), patch("workers.video_clip_worker.publish"):
            # Initial render → regenerate=False, cleanup branch skipped.
            generate_video_clip.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY


# ===========================================================================
# §7 (technical debt) — completed_at is stamped on FAILED jobs too
# ===========================================================================


class TestCompletedAtOnFailedJobs:
    def test_pipeline_failure_stamps_completed_at(self) -> None:
        """Without this, completed_at stays NULL on FAILED → analytics can't
        compute "time-to-failure" or "when did this break"."""
        job = Job.objects.create(
            source_type=SourceType.FILE, status=JobStatus.PENDING
        )
        with patch(
            "workers.tasks.ingest_job",
            side_effect=IngestionError("INGESTION_NORMALIZE_FAILED", "boom"),
        ):
            start_job.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert job.completed_at is not None

    def test_packager_failure_stamps_completed_at(self, tmp_path: Path) -> None:
        """Same guarantee on the packaging-side failure paths."""
        job = _make_full_job(tmp_path, status=JobStatus.GENERATING)
        # No artifacts → PACKAGE_EMPTY path.
        with override_settings(MEDIA_ROOT=str(tmp_path / "media")), patch(
            "workers.packager.publish"
        ):
            (tmp_path / "media" / "packages").mkdir(parents=True, exist_ok=True)
            package_job.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert "PACKAGE_EMPTY" in (job.error or "")
        assert job.completed_at is not None
