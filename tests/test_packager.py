"""Packaging worker — SPEC §§8.2, 9.5."""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
)
from workers.packager import (
    _archive_name,
    package_job,
    render_index_txt,
)
from workers.tasks import check_and_trigger_packaging

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(media_root: Path, status: str = JobStatus.GENERATING) -> Job:
    raw = media_root / "uploads" / "ep.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"\x00" * 16)
    return Job.objects.create(
        source_type=SourceType.FILE,
        raw_media_path=str(raw),
        status=status,
    )


def _make_video(job: Job, idx: int, media_root: Path) -> Artifact:
    rel = f"artifacts/{job.id}/clip_{idx}_v1.mp4"
    abs_path = media_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"VIDEO" + bytes(idx) * 32)
    return Artifact.objects.create(
        job=job,
        type=ArtifactType.VIDEO_CLIP,
        index=idx,
        status=ArtifactStatus.READY,
        file_path=rel,
    )


def _make_text(job: Job, type_: str, content: str) -> Artifact:
    return Artifact.objects.create(
        job=job,
        type=type_,
        index=0,
        status=ArtifactStatus.READY,
        text_content=content,
    )


def _make_quote(job: Job, idx: int, media_root: Path) -> Artifact:
    rel = f"artifacts/{job.id}/quote_{idx}.png"
    abs_path = media_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return Artifact.objects.create(
        job=job,
        type=ArtifactType.QUOTE_GRAPHIC,
        index=idx,
        status=ArtifactStatus.READY,
        file_path=rel,
    )


# ---------------------------------------------------------------------------
# _archive_name + render_index_txt
# ---------------------------------------------------------------------------


def test_archive_name_video_clip():
    art = Artifact(
        job_id="00000000-0000-0000-0000-000000000000",
        type=ArtifactType.VIDEO_CLIP,
        index=2,
        file_path="artifacts/x/clip_2.mp4",
    )
    assert _archive_name(art) == "clips/clip_2.mp4"


def test_archive_name_text_artifacts():
    art = Artifact(type=ArtifactType.LINKEDIN_POST, index=0)
    assert _archive_name(art) == "text/linkedin.md"
    art = Artifact(type=ArtifactType.YOUTUBE_DESCRIPTION, index=0)
    assert _archive_name(art) == "text/youtube_description.txt"


def test_archive_name_quote_graphic():
    art = Artifact(
        type=ArtifactType.QUOTE_GRAPHIC, index=3, file_path="x/quote_3.png"
    )
    assert _archive_name(art) == "graphics/quote_3.png"


def test_render_index_lists_each_artifact(tmp_path):
    job = _make_job(tmp_path)
    Analysis.objects.create(
        job=job,
        episode_title="Test Title",
        hook="Test hook.",
        claude_model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
    )
    arts = [_make_video(job, 0, tmp_path), _make_text(job, ArtifactType.LINKEDIN_POST, "Hi")]
    text = render_index_txt(job, job.analysis, arts)
    assert "Test Title" in text
    assert "Test hook." in text
    assert "clips/clip_0.mp4" in text
    assert "text/linkedin.md" in text


def test_render_index_marks_failed_artifacts(tmp_path):
    job = _make_job(tmp_path)
    failed = Artifact.objects.create(
        job=job,
        type=ArtifactType.LINKEDIN_POST,
        index=0,
        status=ArtifactStatus.FAILED,
        error="Claude returned invalid JSON",
    )
    text = render_index_txt(job, None, [failed])
    assert "[SKIPPED" in text
    assert "Claude returned invalid JSON" in text


# ---------------------------------------------------------------------------
# package_job — happy path
# ---------------------------------------------------------------------------


def test_package_job_writes_zip_and_completes(tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path), MEDIA_URL="/media/"):
        job = _make_job(tmp_path)
        Analysis.objects.create(
            job=job,
            episode_title="Episode One",
            hook="A hook.",
            claude_model="claude-sonnet-4-6",
            input_tokens=0,
            output_tokens=0,
        )
        for idx in range(3):
            _make_video(job, idx, tmp_path)
        _make_text(job, ArtifactType.LINKEDIN_POST, "LinkedIn body")
        _make_text(job, ArtifactType.TWITTER_THREAD, "Tweet 1\n---\nTweet 2")
        _make_quote(job, 0, tmp_path)

        with patch("workers.packager.publish") as publish_mock:
            package_job.apply(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.COMPLETED
        assert job.package_path
        assert job.completed_at is not None

        zip_abs = tmp_path / job.package_path
        assert zip_abs.exists()
        with zipfile.ZipFile(zip_abs) as zf:
            names = zf.namelist()
            assert "index.txt" in names
            assert "clips/clip_0.mp4" in names
            assert "clips/clip_1.mp4" in names
            assert "clips/clip_2.mp4" in names
            assert "text/linkedin.md" in names
            assert "text/twitter_thread.md" in names
            assert "graphics/quote_0.png" in names
            # text content survived the round-trip
            assert zf.read("text/linkedin.md").decode() == "LinkedIn body"

        # SSE 'completed' fires with package_url under MEDIA_URL.
        assert publish_mock.called
        event_args = publish_mock.call_args.args
        assert event_args[1] == "completed"
        assert event_args[2]["package_url"].startswith("/media/packages/")


def test_package_job_includes_partial_when_some_failed(tmp_path):
    """SPEC §9.5 — failed artifacts get noted in index.txt; ready ones still ship."""
    with override_settings(MEDIA_ROOT=str(tmp_path), MEDIA_URL="/media/"):
        job = _make_job(tmp_path)
        _make_video(job, 0, tmp_path)
        Artifact.objects.create(
            job=job,
            type=ArtifactType.LINKEDIN_POST,
            index=0,
            status=ArtifactStatus.FAILED,
            error="Claude returned invalid JSON",
        )

        with patch("workers.packager.publish"):
            package_job.apply(args=[str(job.id)])

        job.refresh_from_db()
        assert job.status == JobStatus.COMPLETED
        zip_abs = tmp_path / job.package_path
        with zipfile.ZipFile(zip_abs) as zf:
            assert "clips/clip_0.mp4" in zf.namelist()
            assert "text/linkedin.md" not in zf.namelist()
            index_text = zf.read("index.txt").decode()
            assert "[SKIPPED: Claude returned invalid JSON]" in index_text


def test_package_job_fails_when_no_ready_artifacts(tmp_path):
    """SPEC §9.5 — every artifact failed → job goes FAILED, no zip."""
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        job = _make_job(tmp_path)
        Artifact.objects.create(
            job=job, type=ArtifactType.VIDEO_CLIP, index=0,
            status=ArtifactStatus.FAILED, error="x",
        )
        with patch("workers.packager.publish"):
            package_job.apply(args=[str(job.id)])
        job.refresh_from_db()
        assert job.status == JobStatus.FAILED
        assert job.package_path is None
        assert "PACKAGE_ALL_FAILED" in (job.error or "")


def test_package_job_idempotent_on_completed(tmp_path):
    """Re-firing on a COMPLETED job is a no-op."""
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        job = _make_job(tmp_path, status=JobStatus.COMPLETED)
        job.package_path = "packages/already.zip"
        job.save()
        with patch("workers.packager.publish") as publish_mock:
            package_job.apply(args=[str(job.id)])
        publish_mock.assert_not_called()
        job.refresh_from_db()
        assert job.package_path == "packages/already.zip"


def test_package_job_unknown_job_id_is_noop():
    package_job.apply(args=["00000000-0000-0000-0000-000000000000"])  # no raise


# ---------------------------------------------------------------------------
# check_and_trigger_packaging
# ---------------------------------------------------------------------------


def test_check_and_trigger_skips_when_pending_artifacts(tmp_path):
    job = _make_job(tmp_path)
    Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY
    )
    Artifact.objects.create(
        job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.QUEUED
    )
    with patch("workers.packager.package_job.apply_async") as enq:
        triggered = check_and_trigger_packaging(str(job.id))
    assert triggered is False
    enq.assert_not_called()


def test_check_and_trigger_fires_when_all_terminal(tmp_path):
    job = _make_job(tmp_path)
    Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY
    )
    Artifact.objects.create(
        job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.FAILED
    )
    with patch("workers.packager.package_job.apply_async") as enq:
        triggered = check_and_trigger_packaging(str(job.id))
    assert triggered is True
    enq.assert_called_once()


def test_check_and_trigger_skips_when_already_packaging(tmp_path):
    job = _make_job(tmp_path, status=JobStatus.PACKAGING)
    Artifact.objects.create(
        job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY
    )
    with patch("workers.packager.package_job.apply_async") as enq:
        triggered = check_and_trigger_packaging(str(job.id))
    assert triggered is False
    enq.assert_not_called()
