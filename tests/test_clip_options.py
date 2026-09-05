"""Pro feature 4 — 9:16 crop layout, caption presets, regenerate hints, partial ZIPs."""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from api.errors import ClipOptionsInvalid
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
from pipeline.ass_subtitles import Word, build_ass, caption_preset, hex_to_ass_colour
from pipeline.clip_options import clean_hint, clip_options_for_job, parse_clip_options
from pipeline.ffmpeg_clip import build_clip_command
from pipeline.prompts.text_artifacts import build_linkedin_prompt
from workers.packager import build_partial_zip
from workers.quote_graphic_worker import generate_quote_graphic
from workers.video_clip_worker import _pick_candidate, generate_video_clip

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


class TestParseClipOptions:
    def test_defaults(self) -> None:
        o = parse_clip_options({})
        assert (o.layout, o.caption_style) == ("pad", "karaoke")

    def test_valid(self) -> None:
        o = parse_clip_options({"clip_layout": " CROP ", "caption_style": "Boxed"})
        assert (o.layout, o.caption_style) == ("crop", "boxed")

    @pytest.mark.parametrize("data,field", [({"clip_layout": "zoom"}, "clip_layout"), ({"caption_style": "neon"}, "caption_style")])
    def test_invalid(self, data: dict, field: str) -> None:
        with pytest.raises(ClipOptionsInvalid) as exc:
            parse_clip_options(data)
        assert exc.value.field == field

    def test_for_job_sanitises_legacy_rows(self) -> None:
        job = Job(source_type=SourceType.FILE, clip_layout="", caption_style="weird")
        o = clip_options_for_job(job)
        assert (o.layout, o.caption_style) == ("pad", "karaoke")

    def test_clean_hint(self) -> None:
        assert clean_hint(None) is None
        assert clean_hint("   ") is None
        assert clean_hint("  shorter ") == "shorter"
        with pytest.raises(ClipOptionsInvalid):
            clean_hint("x" * 301)

    def test_upload_stores_options(self, client: APIClient, tmp_path: Path) -> None:
        f = SimpleUploadedFile("ep.mp3", b"\x00" * 64, content_type="audio/mpeg")
        with override_settings(MEDIA_ROOT=str(tmp_path)), patch("api.views.upload.start_job.apply_async"):
            res = client.post(
                "/api/jobs/upload", {"file": f, "clip_layout": "crop", "caption_style": "clean"}, format="multipart"
            )
        assert res.status_code == 201, res.content
        job = Job.objects.get(id=res.json()["job_id"])
        assert (job.clip_layout, job.caption_style) == ("crop", "clean")

    def test_from_url_rejects_bad_layout(self, client: APIClient) -> None:
        res = client.post("/api/jobs/from_url", {"url": "https://youtu.be/x", "clip_layout": "zoom"}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "CLIP_OPTIONS_INVALID"


# ---------------------------------------------------------------------------
# ffmpeg + ASS
# ---------------------------------------------------------------------------


class TestFfmpegLayout:
    def _vf(self, layout: str) -> str:
        cmd = build_clip_command("in.mp4", 1000, 31000, None, "out.mp4", layout=layout)
        return cmd[cmd.index("-vf") + 1]

    def test_pad_is_default_and_unchanged(self) -> None:
        cmd = build_clip_command("in.mp4", 1000, 31000, None, "out.mp4")
        vf = cmd[cmd.index("-vf") + 1]
        assert vf == self._vf("pad")
        assert "force_original_aspect_ratio=decrease" in vf and "pad=1080:1920" in vf

    def test_crop_fills_and_center_crops(self) -> None:
        vf = self._vf("crop")
        assert "force_original_aspect_ratio=increase" in vf
        assert "crop=1080:1920" in vf
        assert "pad=" not in vf

    def test_crop_keeps_subtitles_filter(self) -> None:
        cmd = build_clip_command("in.mp4", 1000, 31000, "/tmp/s.ass", "out.mp4", layout="crop")
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.endswith("subtitles='/tmp/s.ass'")


class TestCaptionPresets:
    WORDS = [Word("Hello", 1000, 1400), Word("world", 1500, 2000)]

    def test_hex_to_ass(self) -> None:
        assert hex_to_ass_colour("#ff8800") == "&H000088FF"
        assert hex_to_ass_colour("#FF8800") == "&H000088FF"
        assert hex_to_ass_colour("nope") is None
        assert hex_to_ass_colour(None) is None

    def test_karaoke_default_matches_spec(self) -> None:
        ass = build_ass(self.WORDS, 0, 5000)
        assert "Style: Default,Inter,72,&H0000FFFF,&H00FFFFFF" in ass
        assert "{\\k40}Hello {\\k50}world" in ass

    def test_brand_colour_becomes_highlight(self) -> None:
        ass = build_ass(self.WORDS, 0, 5000, highlight_hex="#ff8800")
        assert "Style: Default,Inter,72,&H000088FF," in ass

    def test_clean_has_no_karaoke_tags(self) -> None:
        ass = build_ass(self.WORDS, 0, 5000, style="clean")
        assert "\\k" not in ass
        assert ",,Hello world" in ass
        assert caption_preset("clean").outline == 5

    def test_boxed_uses_border_style_3(self) -> None:
        ass = build_ass(self.WORDS, 0, 5000, style="boxed", highlight_hex="#00ff00")
        style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
        fields = style_line.split(",")
        assert fields[15] == "3"  # BorderStyle
        assert fields[3] == "&H0000FF00"  # highlight in brand colour
        assert "\\k" in ass

    def test_unknown_style_falls_back(self) -> None:
        assert caption_preset("neon").name == "karaoke"


# ---------------------------------------------------------------------------
# worker wiring + hints
# ---------------------------------------------------------------------------


def _full_job(tmp_path: Path, **job_kwargs) -> Job:
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 16)
    job = Job.objects.create(
        source_type=SourceType.FILE, status=JobStatus.COMPLETED, raw_media_path=str(raw),
        duration_sec=300.0, mime_type="video/mp4", brand_color="#ff8800", **job_kwargs,
    )
    Transcript.objects.create(
        job=job, language="en", full_text="x", duration_sec=300.0,
        segments_json=[{"start_ms": 0, "end_ms": 300_000, "words": [{"w": "hi", "start_ms": 1000, "end_ms": 1500}]}],
    )
    Analysis.objects.create(
        job=job, episode_title="t", hook="h", themes_json=[], chapters_json=[],
        clip_candidates_json=[
            {"start_ms": 0, "end_ms": 40_000, "virality_score": 9, "reason": "funny intro", "hook_text": "The intro"},
            {"start_ms": 60_000, "end_ms": 100_000, "virality_score": 8, "reason": "pricing rant", "hook_text": "Why pricing is broken"},
            {"start_ms": 120_000, "end_ms": 160_000, "virality_score": 7, "reason": "hiring story", "hook_text": "Hiring"},
        ],
        quotes_json=[
            {"text": "First quote that is long enough to count", "speaker": "A", "ts_ms": 0},
            {"text": "Second quote that is long enough to count", "speaker": "B", "ts_ms": 0},
        ],
        claude_model="m", input_tokens=1, output_tokens=1,
    )
    return job


class TestPickCandidateWithHint:
    CANDS = [
        {"hook_text": "The intro", "reason": "funny intro"},
        {"hook_text": "Why pricing is broken", "reason": "pricing rant"},
        {"hook_text": "Hiring", "reason": "hiring story"},
    ]

    def test_hint_picks_matching_unused(self) -> None:
        cand, idx = _pick_candidate(self.CANDS, {"used_candidate_indices": [0]}, 0, True, hint="the part about pricing")
        assert idx == 1

    def test_hint_without_match_falls_back_to_next_unused(self) -> None:
        cand, idx = _pick_candidate(self.CANDS, {"used_candidate_indices": [0]}, 0, True, hint="zzz")
        assert idx == 1
        cand, idx = _pick_candidate(self.CANDS, {"used_candidate_indices": [0, 1]}, 0, True, hint="pricing")
        assert idx == 2  # pricing already used → next unused

    def test_no_hint_keeps_old_behaviour(self) -> None:
        _, idx = _pick_candidate(self.CANDS, {"used_candidate_indices": [0, 1]}, 0, True)
        assert idx == 2


class TestVideoWorkerUsesOptions:
    def test_layout_and_caption_style_flow_to_ffmpeg_and_ass(self, tmp_path: Path) -> None:
        job = _full_job(tmp_path, clip_layout="crop", caption_style="boxed")
        artifact = Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0)
        seen: dict = {}

        def fake_ffmpeg(*, output_path: str, ass_path, layout, **_):
            seen["layout"] = layout
            seen["ass"] = Path(ass_path).read_text(encoding="utf-8") if ass_path else None
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 8192)

        with override_settings(MEDIA_ROOT=str(tmp_path / "m"), ARTIFACTS_ROOT=str(tmp_path / "m" / "artifacts")), patch(
            "workers.video_clip_worker.build_vertical_clip", side_effect=fake_ffmpeg
        ), patch("workers.video_clip_worker.publish"):
            generate_video_clip.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert seen["layout"] == "crop"
        assert "&H000088FF" in seen["ass"]  # brand colour highlight
        assert artifact.metadata_json["captions_style"] == "boxed"
        assert artifact.metadata_json["layout"] == "crop"


class TestRegenerateHint:
    def test_hint_stored_and_used_by_text_prompt(self, client: APIClient, tmp_path: Path) -> None:
        job = _full_job(tmp_path)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY, metadata_json={"tone": "casual"}
        )
        prompts: list[str] = []

        def fake_call(transcript_text, user_msg, **_):
            prompts.append(user_msg)
            return "post body " * 20, {"claude_model": "m", "input_tokens": 1, "output_tokens": 1}

        with patch("workers.text_artifact_worker.call_text_artifact", side_effect=fake_call), patch(
            "workers.text_artifact_worker.publish"
        ), patch("workers.packager.package_job.apply_async"):
            res = client.post(f"/api/artifacts/{artifact.id}/regenerate", {"hint": " make it shorter "}, format="json")
        assert res.status_code == 202
        # The worker (eager here) read the hint from the row and put it in
        # the prompt; the READY write then replaces metadata, so the hint is
        # one-shot and doesn't leak into a later plain regenerate.
        assert "<user_request>make it shorter</user_request>" in prompts[0]
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert "regenerate_hint" not in artifact.metadata_json

    def test_hint_is_persisted_until_worker_runs(self, client: APIClient, tmp_path: Path) -> None:
        job = _full_job(tmp_path)
        artifact = Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY)
        with patch("workers.video_clip_worker.generate_video_clip.apply_async"):
            res = client.post(f"/api/artifacts/{artifact.id}/regenerate", {"hint": "pricing"}, format="json")
        assert res.status_code == 202
        artifact.refresh_from_db()
        assert artifact.metadata_json["regenerate_hint"] == "pricing"

    def test_empty_hint_clears_previous(self, client: APIClient, tmp_path: Path) -> None:
        job = _full_job(tmp_path)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY,
            metadata_json={"regenerate_hint": "old"},
        )
        with patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"):
            client.post(f"/api/artifacts/{artifact.id}/regenerate", {}, format="json")
        artifact.refresh_from_db()
        assert "regenerate_hint" not in artifact.metadata_json

    def test_too_long_hint_is_400(self, client: APIClient, tmp_path: Path) -> None:
        job = _full_job(tmp_path)
        artifact = Artifact.objects.create(job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY)
        res = client.post(f"/api/artifacts/{artifact.id}/regenerate", {"hint": "x" * 301}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["field"] == "hint"

    def test_hint_block_in_prompt_builder(self) -> None:
        assert "<user_request>" not in build_linkedin_prompt({"episode_title": "t"}, "casual")


class TestQuoteRegenerateCycles:
    def test_version_advances_quote_and_template(self, tmp_path: Path) -> None:
        job = _full_job(tmp_path)
        artifact = Artifact.objects.create(job=job, type=ArtifactType.QUOTE_GRAPHIC, index=0, version=2)
        seen: dict = {}

        def fake_render(quote, speaker, output_path, *, template_id, branding):
            seen.update(quote=quote, template_id=template_id)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 8)

        with override_settings(MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")), patch(
            "services.graphic_renderer.render_quote_to_png", side_effect=fake_render
        ), patch("workers.quote_graphic_worker.publish"), patch("workers.packager.package_job.apply_async"):
            generate_quote_graphic.apply_async(args=[str(artifact.id)])

        assert seen["quote"].startswith("Second quote")
        assert seen["template_id"] == "gradient_purple"


# ---------------------------------------------------------------------------
# partial download
# ---------------------------------------------------------------------------


class TestPartialDownload:
    def _job(self, media: Path) -> Job:
        job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.GENERATING)
        art_dir = media / "artifacts" / str(job.id)
        art_dir.mkdir(parents=True)
        (art_dir / "clip_0_v1.mp4").write_bytes(b"VIDEO")
        (art_dir / "transcript.srt").write_text("1\n", encoding="utf-8")
        Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=0, status=ArtifactStatus.READY, file_path=f"artifacts/{job.id}/clip_0_v1.mp4")
        Artifact.objects.create(job=job, type=ArtifactType.VIDEO_CLIP, index=1, status=ArtifactStatus.QUEUED)
        Artifact.objects.create(job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY, text_content="post")
        Artifact.objects.create(
            job=job, type=ArtifactType.TRANSCRIPT, index=0, status=ArtifactStatus.READY, text_content="[00:00] hi",
            file_path=f"artifacts/{job.id}/transcript.srt", metadata_json={"files": {"srt": f"artifacts/{job.id}/transcript.srt"}},
        )
        return job

    def test_build_partial_zip(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job = self._job(tmp_path)
            path, n = build_partial_zip(job, "clips")
            with zipfile.ZipFile(path) as zf:
                assert zf.namelist() == ["clips/clip_0.mp4"]
            assert n == 1
            path.unlink()

            path, n = build_partial_zip(job, "text")
            with zipfile.ZipFile(path) as zf:
                assert sorted(zf.namelist()) == ["text/linkedin.md", "text/transcript.txt"]
            path.unlink()

            path, n = build_partial_zip(job, "subtitles")
            with zipfile.ZipFile(path) as zf:
                assert zf.namelist() == ["subtitles/transcript.srt"]
            path.unlink()

    def test_endpoint_streams_part_even_while_generating(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job = self._job(tmp_path)
            res = client.get(f"/api/jobs/{job.id}/download?part=clips")
            assert res.status_code == 200
            assert res["Content-Disposition"].endswith('_clips.zip"')
            body = b"".join(res.streaming_content)
        import io

        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            assert zf.read("clips/clip_0.mp4") == b"VIDEO"

    def test_endpoint_404_when_nothing_ready(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            job = self._job(tmp_path)
            res = client.get(f"/api/jobs/{job.id}/download?part=graphics")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "PACKAGE_NOT_READY"

    def test_endpoint_rejects_unknown_part(self, client: APIClient) -> None:
        job = Job.objects.create(source_type=SourceType.FILE)
        res = client.get(f"/api/jobs/{job.id}/download?part=everything")
        assert res.status_code == 400
