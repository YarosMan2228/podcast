"""End-to-end integration test for the Day-3 pipeline.

Exercises the full Celery chain in eager mode:

    start_job → transcribe_job_task → analyze_job_task
               → orchestrate_artifacts → generate_video_clip (x5)

External boundaries (ffmpeg / ffprobe / Whisper / Claude) are stubbed at
the module edge; everything between — ingestion normalisation, transcript
stitching, analysis validation, clip fan-out, video worker bookkeeping —
runs real. This is the first test that would catch a wiring regression
when any single stage is refactored in isolation.
"""
from __future__ import annotations

import json
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
from services.claude_client import ClaudeResponse
from workers.tasks import NUM_VIDEO_CLIPS, start_job

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Stub payloads — minimal shapes that match the contracts between stages
# ---------------------------------------------------------------------------


def _fake_transcript_payload(duration_sec: float) -> dict:
    """SPEC §1.4 — one segment per clip candidate with word-level timestamps."""
    segments = []
    for i in range(6):
        seg_start = i * 60_000
        segments.append(
            {
                "id": i,
                "start_ms": seg_start,
                "end_ms": seg_start + 50_000,
                "text": f"Segment {i} text with several words in it.",
                "words": [
                    {
                        "w": f"word{i}a",
                        "start_ms": seg_start + 1000,
                        "end_ms": seg_start + 1500,
                    },
                    {
                        "w": f"word{i}b",
                        "start_ms": seg_start + 1600,
                        "end_ms": seg_start + 2100,
                    },
                    {
                        "w": f"word{i}c",
                        "start_ms": seg_start + 2200,
                        "end_ms": seg_start + 2700,
                    },
                ],
            }
        )
    return {
        "language": "en",
        "full_text": " ".join(seg["text"] for seg in segments),
        "duration_sec": duration_sec,
        "segments": segments,
    }


def _fake_analysis_json() -> str:
    """Schema-valid response (SPEC §1.5) the real validator will accept."""
    payload = {
        "episode_title": "Integration Test Episode",
        "hook": "A compact demo episode for integration tests.",
        "guest": None,
        "themes": ["integration", "pipeline", "testing"],
        "chapters": [
            {"start_ms": 0, "end_ms": 60_000, "title": "Intro"},
            {"start_ms": 60_000, "end_ms": 360_000, "title": "Body"},
        ],
        # Six candidates — orchestrator must clamp to NUM_VIDEO_CLIPS (5).
        "clip_candidates": [
            {
                "start_ms": i * 60_000 + 1000,
                "end_ms": i * 60_000 + 45_000,
                "virality_score": 9 - i,
                "reason": f"reason {i}",
                "hook_text": f"hook {i}",
            }
            for i in range(6)
        ],
        "notable_quotes": [
            {"text": "A notable quote.", "speaker": None, "ts_ms": 5_000},
        ],
    }
    return json.dumps(payload)


def _ffmpeg_stub_writes_output(**kwargs) -> None:
    """Stand-in for build_vertical_clip: just write enough bytes to pass the
    worker's size gate."""
    out = Path(kwargs["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00" * 8192)


# ---------------------------------------------------------------------------
# End-to-end flow
# ---------------------------------------------------------------------------


def test_full_pipeline_upload_to_video_clips(tmp_path: Path) -> None:
    """Upload through all five stages yields five READY video clips."""
    raw = tmp_path / "episode.mp4"
    raw.write_bytes(b"\x00" * 16)

    job = Job.objects.create(
        source_type=SourceType.FILE,
        status=JobStatus.PENDING,
        raw_media_path=str(raw),
        mime_type="video/mp4",
        original_filename="episode.mp4",
    )

    duration_sec = 360.0
    claude_response = ClaudeResponse(
        text=_fake_analysis_json(),
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_creation_tokens=1000,
        stop_reason="end_turn",
    )

    with override_settings(
        MEDIA_ROOT=str(tmp_path / "media"),
        ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
    ), patch(
        "pipeline.ingestion.normalize_to_wav",
        side_effect=lambda _src, dst: Path(dst).write_bytes(b"\x00" * 4096),
    ), patch(
        "pipeline.ingestion.probe_duration_sec", return_value=duration_sec
    ), patch(
        "pipeline.transcription.transcribe_file",
        return_value=_fake_transcript_payload(duration_sec),
    ), patch(
        "services.claude_client.claude_client.call", return_value=claude_response
    ), patch(
        "workers.video_clip_worker.build_vertical_clip",
        side_effect=_ffmpeg_stub_writes_output,
    ) as ff_clip, patch(
        "workers.text_artifact_worker.generate_linkedin_post.apply_async"
    ), patch(
        "workers.text_artifact_worker.generate_twitter_thread.apply_async"
    ), patch(
        "workers.text_artifact_worker.generate_show_notes.apply_async"
    ), patch(
        "workers.text_artifact_worker.generate_newsletter.apply_async"
    ), patch(
        "workers.text_artifact_worker.generate_youtube_description.apply_async"
    ), patch(
        "workers.quote_graphic_worker.generate_quote_graphic.apply_async"
    ):
        start_job.apply_async(args=[str(job.id)])

    # -------- Job reached the fan-out stage --------
    job.refresh_from_db()
    # Day-3 scope ends at GENERATING: text/graphics workers (Person B) and
    # the Day-5 packager haven't run, so we don't transition further.
    assert job.status == JobStatus.GENERATING
    assert job.duration_sec == duration_sec
    assert job.normalized_wav_path, "ingestion should have persisted normalized wav path"

    # -------- Transcript + Analysis rows populated --------
    transcript = Transcript.objects.get(job=job)
    assert transcript.language == "en"
    assert transcript.segments_json, "segments should have been stitched in"

    analysis = Analysis.objects.get(job=job)
    assert analysis.episode_title == "Integration Test Episode"
    # Six candidates in, deduper shouldn't drop any (they don't overlap).
    assert len(analysis.clip_candidates_json) == 6

    # -------- Exactly NUM_VIDEO_CLIPS VIDEO_CLIP artifacts, all READY --------
    artifacts = list(
        Artifact.objects.filter(job=job, type=ArtifactType.VIDEO_CLIP).order_by("index")
    )
    assert len(artifacts) == NUM_VIDEO_CLIPS
    for idx, art in enumerate(artifacts):
        assert art.status == ArtifactStatus.READY, f"clip {idx} not ready: {art.error}"
        assert art.index == idx
        assert art.file_path and art.file_path.endswith(f"clip_{idx}_v1.mp4")
        meta = art.metadata_json
        assert meta["source_clip_candidate_index"] == idx
        assert meta["resolution"] == "1080x1920"
        assert meta["captions_style"] == "karaoke_white_yellow"

    # -------- ffmpeg was called once per clip --------
    assert ff_clip.call_count == NUM_VIDEO_CLIPS
    # And each got the correct candidate window (SPEC §5.4 clip selection).
    for call, candidate in zip(ff_clip.call_args_list, analysis.clip_candidates_json):
        kwargs = call.kwargs
        assert kwargs["start_ms"] == candidate["start_ms"]
        assert kwargs["end_ms"] == candidate["end_ms"]
        # Subtitles path is either None (no words in window) or a real
        # /tmp/sub_*.ass path — the worker cleaned it up after the call
        # returned, so we don't assert it exists, just that it's plausible.
        assert kwargs["ass_path"] is None or kwargs["ass_path"].endswith(".ass")


def test_full_pipeline_halts_job_if_analysis_fails(tmp_path: Path) -> None:
    """A broken Claude response should FAIL the job before fan-out."""
    raw = tmp_path / "episode.mp4"
    raw.write_bytes(b"\x00" * 16)

    job = Job.objects.create(
        source_type=SourceType.FILE,
        raw_media_path=str(raw),
        mime_type="video/mp4",
    )

    # Malformed JSON: validator rejects it on every retry.
    bad_response = ClaudeResponse(
        text="not json at all",
        input_tokens=100,
        output_tokens=10,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        stop_reason="end_turn",
    )

    with override_settings(
        MEDIA_ROOT=str(tmp_path / "media"),
        ARTIFACTS_ROOT=str(tmp_path / "media" / "artifacts"),
    ), patch(
        "pipeline.ingestion.normalize_to_wav",
        side_effect=lambda _src, dst: Path(dst).write_bytes(b"\x00" * 4096),
    ), patch(
        "pipeline.ingestion.probe_duration_sec", return_value=60.0
    ), patch(
        "pipeline.transcription.transcribe_file",
        return_value=_fake_transcript_payload(60.0),
    ), patch(
        "services.claude_client.claude_client.call", return_value=bad_response
    ), patch(
        "workers.video_clip_worker.build_vertical_clip"
    ) as ff_clip:
        start_job.apply_async(args=[str(job.id)])

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "ANALYSIS_INVALID_JSON" in (job.error or "")
    # Fan-out never happened; no clips were attempted.
    assert not Artifact.objects.filter(job=job).exists()
    ff_clip.assert_not_called()
