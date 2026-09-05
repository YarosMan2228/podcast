"""Pro feature 1 — any language + TRANSCRIPT artifact (txt / SRT / VTT)."""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

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
from pipeline.prompts.analysis import build_messages
from pipeline.prompts.languages import language_name, output_language_instruction
from pipeline.prompts.text_artifacts import build_linkedin_prompt, build_twitter_prompt
from pipeline.subtitles import (
    build_cues,
    build_plain_transcript,
    build_srt,
    build_vtt,
    format_srt_time,
    format_vtt_time,
)
from workers.packager import package_job
from workers.tasks import orchestrate_artifacts
from workers.transcript_worker import generate_transcript

pytestmark = pytest.mark.django_db

SEGMENTS = [
    {
        "id": 0,
        "start_ms": 0,
        "end_ms": 2_500,
        "text": "Привет, это подкаст.",
        "words": [
            {"w": "Привет,", "start_ms": 0, "end_ms": 600},
            {"w": "это", "start_ms": 700, "end_ms": 900},
            {"w": "подкаст.", "start_ms": 1000, "end_ms": 2500},
        ],
    },
    {"id": 1, "start_ms": 2_600, "end_ms": 4_000, "text": "Сегодня про деньги.", "words": []},
    # Long pause → new paragraph in the plain transcript.
    {"id": 2, "start_ms": 9_000, "end_ms": 11_000, "text": "Вторая часть.", "words": []},
    {"id": 3, "start_ms": 11_100, "end_ms": 11_200, "text": "   ", "words": []},  # empty → dropped
]


def _make_job(tmp_path: Path, *, language: str = "ru") -> Job:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.GENERATING)
    Transcript.objects.create(
        job=job,
        language=language,
        full_text="Привет, это подкаст. Сегодня про деньги. Вторая часть.",
        segments_json=SEGMENTS,
        duration_sec=11.0,
    )
    return job


# ---------------------------------------------------------------------------
# subtitles.py
# ---------------------------------------------------------------------------


class TestSubtitleBuilders:
    def test_timestamp_formats(self) -> None:
        assert format_srt_time(3_723_456) == "01:02:03,456"
        assert format_vtt_time(3_723_456) == "01:02:03.456"
        assert format_srt_time(-5) == "00:00:00,000"

    def test_srt_shape(self) -> None:
        srt = build_srt(SEGMENTS)
        blocks = [b for b in srt.strip().split("\n\n") if b]
        assert len(blocks) == 3  # empty segment dropped
        assert blocks[0].splitlines() == ["1", "00:00:00,000 --> 00:00:02,500", "Привет, это подкаст."]
        assert blocks[2].startswith("3\n00:00:09,000 --> 00:00:11,000")

    def test_vtt_shape(self) -> None:
        vtt = build_vtt(SEGMENTS)
        assert vtt.startswith("WEBVTT\n\n")
        assert "00:00:02.600 --> 00:00:04.000\nСегодня про деньги." in vtt

    def test_long_segment_is_split_on_word_boundaries(self) -> None:
        words = [
            {"w": f"word{i}", "start_ms": i * 100, "end_ms": i * 100 + 90} for i in range(40)
        ]
        seg = {"start_ms": 0, "end_ms": 4000, "text": " ".join(w["w"] for w in words), "words": words}
        cues = build_cues([seg], max_chars=60)
        assert len(cues) > 1
        assert all(len(c.text) <= 60 for c in cues)
        assert cues[0].start_ms == 0 and cues[-1].end_ms == 3990
        assert " ".join(c.text for c in cues) == seg["text"]

    def test_zero_length_cue_gets_minimum_duration(self) -> None:
        cues = build_cues([{"start_ms": 1000, "end_ms": 1000, "text": "hi"}])
        assert cues[0].end_ms > cues[0].start_ms

    def test_plain_transcript_paragraphs(self) -> None:
        text = build_plain_transcript(SEGMENTS)
        paragraphs = text.strip().split("\n\n")
        assert paragraphs == [
            "[00:00] Привет, это подкаст. Сегодня про деньги.",
            "[00:09] Вторая часть.",
        ]

    def test_empty_input(self) -> None:
        assert build_srt([]) == ""
        assert build_vtt([]) == "WEBVTT\n"
        assert build_plain_transcript([]) == ""


# ---------------------------------------------------------------------------
# language prompts
# ---------------------------------------------------------------------------


class TestLanguagePrompts:
    def test_language_names(self) -> None:
        assert language_name("uk") == "Ukrainian"
        assert language_name(None) == "English"
        assert language_name("xx") == "xx"

    def test_english_adds_nothing(self) -> None:
        assert output_language_instruction("en") == ""
        assert output_language_instruction(None) == ""
        _, msgs = build_messages(full_text="hi", segments=[], language="en")
        _, msgs_default = build_messages(full_text="hi", segments=[])
        assert msgs == msgs_default

    def test_analysis_prompt_mentions_language(self) -> None:
        _, msgs = build_messages(full_text="привет", segments=[], language="ru")
        assert "Russian" in msgs[0]["content"]
        assert "JSON keys stay in English" in msgs[0]["content"]

    def test_text_prompts_carry_language_and_hint(self) -> None:
        analysis = {"episode_title": "t", "hook": "h", "language": "uk", "regenerate_hint": "коротше"}
        for builder in (build_linkedin_prompt, build_twitter_prompt):
            prompt = builder(analysis, "casual")
            assert "<language>" in prompt and "Ukrainian" in prompt
            assert "<user_request>коротше</user_request>" in prompt
            # Placeholders survive the f-string.
        assert "{{EPISODE_URL}}" in build_twitter_prompt(analysis, "casual")

    def test_text_prompt_without_language_is_unchanged(self) -> None:
        prompt = build_linkedin_prompt({"episode_title": "t"}, "casual")
        assert "<language>" not in prompt and "<user_request>" not in prompt


# ---------------------------------------------------------------------------
# transcript worker + orchestrator + packager + API
# ---------------------------------------------------------------------------


class TestTranscriptWorker:
    def test_happy_path(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.TRANSCRIPT, index=0, status=ArtifactStatus.QUEUED
        )
        with override_settings(
            MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")
        ), patch("workers.text_artifact_worker.publish"):
            generate_transcript.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.text_content.startswith("[00:00] Привет")
        assert artifact.file_path == f"artifacts/{job.id}/transcript.srt"
        files = artifact.metadata_json["files"]
        assert (tmp_path / files["srt"]).read_text(encoding="utf-8").startswith("1\n")
        assert (tmp_path / files["vtt"]).read_text(encoding="utf-8").startswith("WEBVTT")
        assert artifact.metadata_json["language"] == "ru"
        assert artifact.metadata_json["word_count"] == 8

    def test_missing_transcript_fails_immediately(self, tmp_path: Path) -> None:
        job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.GENERATING)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.TRANSCRIPT, index=0, status=ArtifactStatus.QUEUED
        )
        with override_settings(MEDIA_ROOT=str(tmp_path)), patch(
            "workers.text_artifact_worker.publish"
        ):
            generate_transcript.apply_async(args=[str(artifact.id)])  # no retry storm
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED

    def test_api_exposes_file_urls(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        Artifact.objects.create(
            job=job,
            type=ArtifactType.TRANSCRIPT,
            index=0,
            status=ArtifactStatus.READY,
            file_path=f"artifacts/{job.id}/transcript.srt",
            metadata_json={"files": {"srt": f"artifacts/{job.id}/transcript.srt", "vtt": f"artifacts/{job.id}/transcript.vtt"}},
        )
        res = APIClient().get(f"/api/jobs/{job.id}")
        art = res.json()["artifacts"][0]
        assert art["files"] == {
            "srt": f"/media/artifacts/{job.id}/transcript.srt",
            "vtt": f"/media/artifacts/{job.id}/transcript.vtt",
        }


class TestOrchestratorTranscriptSlot:
    def test_creates_transcript_slot(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        job.status = JobStatus.ANALYZING
        job.save()
        Analysis.objects.create(
            job=job,
            episode_title="t",
            hook="h",
            themes_json=[],
            chapters_json=[],
            clip_candidates_json=[
                {"start_ms": 0, "end_ms": 45_000, "virality_score": 9, "reason": "r", "hook_text": "a"}
            ],
            quotes_json=[],
            claude_model="m",
            input_tokens=1,
            output_tokens=1,
        )
        with (
            patch("workers.video_clip_worker.generate_video_clip.apply_async"),
            patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"),
            patch("workers.text_artifact_worker.generate_twitter_thread.apply_async"),
            patch("workers.text_artifact_worker.generate_show_notes.apply_async"),
            patch("workers.text_artifact_worker.generate_newsletter.apply_async"),
            patch("workers.text_artifact_worker.generate_youtube_description.apply_async"),
            patch("workers.transcript_worker.generate_transcript.apply_async") as kick,
        ):
            orchestrate_artifacts.apply_async(args=[str(job.id)])
        assert Artifact.objects.filter(job=job, type=ArtifactType.TRANSCRIPT).count() == 1
        kick.assert_called_once()
        assert kick.call_args.kwargs["queue"] == "text_artifacts"


class TestPackagerTranscript:
    def test_zip_contains_txt_and_subtitles(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job = _make_job(tmp_path)
            art_dir = tmp_path / "artifacts" / str(job.id)
            art_dir.mkdir(parents=True)
            (art_dir / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
            (art_dir / "transcript.vtt").write_text("WEBVTT\n", encoding="utf-8")
            Artifact.objects.create(
                job=job,
                type=ArtifactType.TRANSCRIPT,
                index=0,
                status=ArtifactStatus.READY,
                text_content="[00:00] hi",
                file_path=f"artifacts/{job.id}/transcript.srt",
                metadata_json={
                    "files": {
                        "srt": f"artifacts/{job.id}/transcript.srt",
                        "vtt": f"artifacts/{job.id}/transcript.vtt",
                    }
                },
            )
            with patch("workers.packager.publish"):
                package_job.apply(args=[str(job.id)])
            job.refresh_from_db()
            assert job.status == JobStatus.COMPLETED
            with zipfile.ZipFile(tmp_path / job.package_path) as zf:
                names = set(zf.namelist())
                assert {"text/transcript.txt", "subtitles/transcript.srt", "subtitles/transcript.vtt"} <= names
                assert zf.read("text/transcript.txt").decode() == "[00:00] hi"
                assert "subtitles/" in zf.read("index.txt").decode()
