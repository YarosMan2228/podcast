"""workers.video_clip_worker — Celery task rendering one VIDEO_CLIP.

``build_vertical_clip`` is stubbed out (ffmpeg itself is covered by
``test_ffmpeg_clip.py``). The tests here focus on orchestration:

* artifact status transitions QUEUED → PROCESSING → READY/FAILED,
* candidate selection for initial vs. regenerate paths,
* SSE event emission,
* Celery decorator conformance (rules/celery-tasks.md §1).
"""
from __future__ import annotations

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
    Transcript,
)
from workers.video_clip_worker import generate_video_clip

pytestmark = pytest.mark.django_db


# ---------- fixtures ----------


def _make_full_job(tmp_path: Path, *, duration_sec: float = 120.0) -> Job:
    """Build a Job row ready for the video worker to consume."""
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 16)
    job = Job.objects.create(
        source_type=SourceType.FILE,
        status=JobStatus.GENERATING,
        raw_media_path=str(raw),
        duration_sec=duration_sec,
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
        duration_sec=duration_sec,
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
                "reason": "strong",
                "hook_text": "the hook",
            },
            {
                "start_ms": 50_000,
                "end_ms": 85_000,
                "virality_score": 7,
                "reason": "good",
                "hook_text": "hook two",
            },
        ],
        quotes_json=[],
        claude_model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
    )
    return job


def _make_artifact(job: Job, index: int = 0) -> Artifact:
    return Artifact.objects.create(
        job=job,
        type=ArtifactType.VIDEO_CLIP,
        index=index,
        status=ArtifactStatus.QUEUED,
    )


def _ffmpeg_stub_writes(tmp_path: Path):
    """Return a side_effect for build_vertical_clip that creates the output file."""

    def _write(*, output_path: str, **_kwargs) -> None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x00" * 8192)

    return _write


# ---------- decorator ----------


def test_video_clip_task_uses_standard_decorator() -> None:
    """rules/celery-tasks.md §1 — every Celery task ships with the same knobs."""
    assert generate_video_clip.max_retries == 3
    assert generate_video_clip.soft_time_limit == 300
    assert generate_video_clip.time_limit == 330
    assert generate_video_clip.acks_late is True


# ---------- happy path ----------


def test_generate_video_clip_happy_path(tmp_path: Path) -> None:
    job = _make_full_job(tmp_path)
    artifact = _make_artifact(job, index=0)

    with override_settings(
        MEDIA_ROOT=str(tmp_path / "media"),
        ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
    ), patch(
        "workers.video_clip_worker.build_vertical_clip",
        side_effect=_ffmpeg_stub_writes(tmp_path),
    ) as ff, patch("workers.video_clip_worker.publish") as pub:
        generate_video_clip.apply_async(args=[str(artifact.id)])

    ff.assert_called_once()
    # Artifact ended up READY with a relative file_path and metadata populated.
    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.READY
    assert artifact.file_path and artifact.file_path.endswith(
        "clip_0_v1.mp4"
    )
    assert artifact.metadata_json["source_clip_candidate_index"] == 0
    assert artifact.metadata_json["virality_score"] == 9
    assert 0 in artifact.metadata_json["used_candidate_indices"]
    # SSE fan-out fired.
    pub.assert_called_once_with(
        str(job.id),
        "artifact_ready",
        {"artifact_id": str(artifact.id), "type": ArtifactType.VIDEO_CLIP, "index": 0},
    )


def test_generate_video_clip_transitions_to_processing_before_ffmpeg(
    tmp_path: Path,
) -> None:
    """Status must flip QUEUED → PROCESSING before ffmpeg runs — otherwise
    a second kick could try to render the same artifact in parallel."""
    job = _make_full_job(tmp_path)
    artifact = _make_artifact(job)

    observed: dict[str, str] = {}

    def _capture(**_kwargs) -> None:
        # Mid-flight: the row should read PROCESSING.
        observed["mid"] = Artifact.objects.get(id=artifact.id).status
        Path(_kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(_kwargs["output_path"]).write_bytes(b"\x00" * 8192)

    with override_settings(
        MEDIA_ROOT=str(tmp_path / "media"),
        ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
    ), patch(
        "workers.video_clip_worker.build_vertical_clip", side_effect=_capture
    ), patch("workers.video_clip_worker.publish"):
        generate_video_clip.apply_async(args=[str(artifact.id)])

    assert observed["mid"] == ArtifactStatus.PROCESSING


# ---------- failure paths ----------


def test_generate_video_clip_ffmpeg_failure_marks_failed(tmp_path: Path) -> None:
    from pipeline.ffmpeg_clip import FFmpegClipError

    job = _make_full_job(tmp_path)
    artifact = _make_artifact(job)

    with override_settings(
        MEDIA_ROOT=str(tmp_path / "media"),
        ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
    ), patch(
        "workers.video_clip_worker.build_vertical_clip",
        side_effect=FFmpegClipError("CLIP_FFMPEG_FAILED", "ffmpeg exited 1"),
    ), patch("workers.video_clip_worker.publish") as pub:
        generate_video_clip.apply_async(args=[str(artifact.id)])

    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.FAILED
    assert "CLIP_FFMPEG_FAILED" in artifact.error
    # artifact_failed event was published for the frontend.
    pub.assert_called_once()
    assert pub.call_args.args[1] == "artifact_failed"


def test_generate_video_clip_no_candidates_fails(tmp_path: Path) -> None:
    """Analysis with zero clip_candidates → the worker can't invent one."""
    job = _make_full_job(tmp_path)
    Analysis.objects.filter(job=job).update(clip_candidates_json=[])
    artifact = _make_artifact(job)

    with patch("workers.video_clip_worker.build_vertical_clip") as ff, patch(
        "workers.video_clip_worker.publish"
    ):
        generate_video_clip.apply_async(args=[str(artifact.id)])

    ff.assert_not_called()
    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.FAILED
    assert "CLIP_INVALID_INPUT" in artifact.error


# ---------- regenerate path ----------


def test_regenerate_picks_next_unused_candidate(tmp_path: Path) -> None:
    """SPEC §5.4: regenerate advances to the next unused clip candidate
    and increments ``version`` so the output filename doesn't collide."""
    job = _make_full_job(tmp_path)
    artifact = _make_artifact(job, index=0)
    # Simulate initial render already done with candidate 0 used.
    artifact.metadata_json = {"used_candidate_indices": [0]}
    artifact.status = ArtifactStatus.READY
    artifact.save()

    with override_settings(
        MEDIA_ROOT=str(tmp_path / "media"),
        ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
    ), patch(
        "workers.video_clip_worker.build_vertical_clip",
        side_effect=_ffmpeg_stub_writes(tmp_path),
    ), patch("workers.video_clip_worker.publish"):
        generate_video_clip.apply_async(args=[str(artifact.id), True])

    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.READY
    assert artifact.version == 2  # bumped
    assert artifact.metadata_json["source_clip_candidate_index"] == 1
    assert set(artifact.metadata_json["used_candidate_indices"]) == {0, 1}
    assert artifact.file_path.endswith("clip_0_v2.mp4")


def test_missing_artifact_is_noop() -> None:
    """A kick for an id that doesn't exist must not crash the worker —
    just log and return (happens if the row was deleted mid-retry)."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    # Shouldn't raise.
    generate_video_clip.apply_async(args=[fake_id])
