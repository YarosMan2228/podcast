"""Pro feature 3 — per-job branding (name / colour / logo) + EPISODE_THUMBNAIL."""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from api.errors import BrandingInvalid
from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
)
from pipeline.branding import branding_for_job, logo_data_uri, parse_branding
from services.graphic_renderer import _fill_template, _fill_thumbnail_template
from workers.tasks import orchestrate_artifacts
from workers.thumbnail_worker import generate_thumbnail

pytestmark = pytest.mark.django_db

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _audio(name: str = "ep.mp3") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00" * 64, content_type="audio/mpeg")


def _logo(name: str = "logo.png", mime: str = "image/png", data: bytes = PNG_BYTES) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=mime)


# ---------------------------------------------------------------------------
# parse_branding
# ---------------------------------------------------------------------------


class TestParseBranding:
    def test_defaults(self) -> None:
        b = parse_branding({}, {})
        assert b.podcast_name is None
        assert b.brand_color == "#6366f1"
        assert b.logo is None

    def test_valid_values(self) -> None:
        b = parse_branding({"podcast_name": "  My Show ", "brand_color": "#FF8800"}, {"logo": _logo()})
        assert b.podcast_name == "My Show"
        assert b.brand_color == "#ff8800"
        assert b.logo is not None

    @pytest.mark.parametrize("color", ["red", "#fff", "123456", "#12345g"])
    def test_bad_color(self, color: str) -> None:
        with pytest.raises(BrandingInvalid) as exc:
            parse_branding({"brand_color": color}, {})
        assert exc.value.field == "brand_color"

    def test_name_too_long(self) -> None:
        with pytest.raises(BrandingInvalid):
            parse_branding({"podcast_name": "x" * 121}, {})

    def test_logo_validation(self) -> None:
        with pytest.raises(BrandingInvalid):
            parse_branding({}, {"logo": _logo(mime="application/pdf", name="x.pdf")})
        with pytest.raises(BrandingInvalid):
            parse_branding({}, {"logo": _logo(data=b"")})
        with pytest.raises(BrandingInvalid):
            parse_branding({}, {"logo": _logo(data=b"\x00" * (2 * 1024 * 1024 + 1))})
        # octet-stream with a .png name is accepted via extension sniff.
        b = parse_branding({}, {"logo": _logo(mime="application/octet-stream")})
        assert b.logo.content_type == "image/png"


# ---------------------------------------------------------------------------
# upload / from_url store branding
# ---------------------------------------------------------------------------


class TestUploadBranding:
    def test_upload_persists_branding_and_logo(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)), patch("api.views.upload.start_job.apply_async"):
            res = client.post(
                "/api/jobs/upload",
                {"file": _audio(), "podcast_name": "Deep Talk", "brand_color": "#00aa88", "logo": _logo()},
                format="multipart",
            )
        assert res.status_code == 201, res.content
        job = Job.objects.get(id=res.json()["job_id"])
        assert job.podcast_name == "Deep Talk"
        assert job.brand_color == "#00aa88"
        assert job.logo_path == f"uploads/{job.id}/logo.png"
        assert (tmp_path / job.logo_path).read_bytes() == PNG_BYTES

    def test_upload_rejects_bad_color_before_writing(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            res = client.post(
                "/api/jobs/upload", {"file": _audio(), "brand_color": "blue"}, format="multipart"
            )
        assert res.status_code == 400
        assert res.json()["error"] == {"code": "BRANDING_INVALID", "message": "Invalid brand_color: expected #RRGGBB.", "field": "brand_color"}
        assert Job.objects.count() == 0

    def test_from_url_json_branding(self, client: APIClient) -> None:
        with patch("api.views.upload.start_job.apply_async"):
            res = client.post(
                "/api/jobs/from_url",
                {"url": "https://www.youtube.com/watch?v=abc", "podcast_name": "URL Show", "brand_color": "#123456"},
                format="json",
            )
        assert res.status_code == 201, res.content
        job = Job.objects.get(id=res.json()["job_id"])
        assert job.podcast_name == "URL Show" and job.brand_color == "#123456"

    def test_from_url_multipart_with_logo(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)), patch("api.views.upload.start_job.apply_async"):
            res = client.post(
                "/api/jobs/from_url",
                {"url": "https://youtu.be/abc", "logo": _logo()},
                format="multipart",
            )
        assert res.status_code == 201, res.content
        job = Job.objects.get(id=res.json()["job_id"])
        assert job.logo_path and (tmp_path / job.logo_path).exists()

    def test_job_payload_exposes_branding(self, client: APIClient) -> None:
        job = Job.objects.create(
            source_type=SourceType.FILE, podcast_name="P", brand_color="#abcdef", logo_path="uploads/x/logo.png"
        )
        body = client.get(f"/api/jobs/{job.id}").json()
        assert body["branding"] == {
            "podcast_name": "P",
            "brand_color": "#abcdef",
            "logo_url": "/media/uploads/x/logo.png",
        }


# ---------------------------------------------------------------------------
# renderer templates
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_logo_data_uri(self, tmp_path: Path) -> None:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            p = tmp_path / "uploads" / "j" / "logo.png"
            p.parent.mkdir(parents=True)
            p.write_bytes(PNG_BYTES)
            uri = logo_data_uri("uploads/j/logo.png")
            assert uri.startswith("data:image/png;base64,")
            assert base64.b64decode(uri.split(",", 1)[1]) == PNG_BYTES
            assert logo_data_uri("uploads/j/missing.png") is None
            assert logo_data_uri(None) is None

    def test_branding_for_job_defaults_and_sanitises(self) -> None:
        job = Job(source_type=SourceType.FILE, brand_color="not-a-color")
        b = branding_for_job(job)
        assert b == {"podcast_name": "Podcast Pack", "brand_color": "#6366f1", "logo_data_uri": None}

    def test_quote_templates_use_branding(self) -> None:
        branding = {"podcast_name": "A <B>", "brand_color": "#ff0000", "logo_data_uri": "data:image/png;base64,AAAA"}
        for tpl in ("minimal_dark", "gradient_purple"):
            html = _fill_template(tpl, "Quote", "Speaker", branding)
            assert "#ff0000" in html
            assert "A &lt;B&gt;" in html
            assert '<img class="logo" src="data:image/png;base64,AAAA"' in html
            assert "{{" not in html, f"unfilled placeholder in {tpl}"

    def test_quote_template_without_logo(self) -> None:
        html = _fill_template("minimal_dark", "Q", "S", None)
        assert "<img" not in html and "{{" not in html and "Podcast Pack" in html

    def test_thumbnail_template(self) -> None:
        html = _fill_thumbnail_template(
            "thumbnail_default", "Short", "The hook", {"podcast_name": "P", "brand_color": "#112233", "logo_data_uri": None}
        )
        assert "Short" in html and "The hook" in html and "#112233" in html
        assert "{{" not in html
        assert "font-size: 84px" in html
        long_html = _fill_thumbnail_template("thumbnail_default", "x" * 60, "", None)
        assert "font-size: 64px" in long_html


# ---------------------------------------------------------------------------
# thumbnail worker + orchestrator slot
# ---------------------------------------------------------------------------


def _job_with_analysis() -> Job:
    job = Job.objects.create(
        source_type=SourceType.FILE, status=JobStatus.GENERATING, podcast_name="Show", brand_color="#00ff00"
    )
    Analysis.objects.create(
        job=job, episode_title="Title", hook="Hook", themes_json=[], chapters_json=[],
        clip_candidates_json=[{"start_ms": 0, "end_ms": 45_000, "virality_score": 9, "reason": "r", "hook_text": "a"}],
        quotes_json=[], claude_model="m", input_tokens=1, output_tokens=1,
    )
    return job


class TestThumbnailWorker:
    def test_happy_path(self, tmp_path: Path) -> None:
        job = _job_with_analysis()
        artifact = Artifact.objects.create(job=job, type=ArtifactType.EPISODE_THUMBNAIL, index=0)
        captured: dict = {}

        def fake_render(title, hook, output_path, *, template_id, branding):
            captured.update(title=title, hook=hook, template_id=template_id, branding=branding)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(PNG_BYTES)

        with override_settings(MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")), patch(
            "services.graphic_renderer.render_thumbnail_to_png", side_effect=fake_render
        ), patch("workers.quote_graphic_worker.publish"):
            generate_thumbnail.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.file_path == f"artifacts/{job.id}/thumbnail.png"
        assert captured["title"] == "Title" and captured["hook"] == "Hook"
        assert captured["branding"]["podcast_name"] == "Show"
        assert captured["branding"]["brand_color"] == "#00ff00"
        assert artifact.metadata_json["resolution"] == "1280x720"

    def test_missing_playwright_fails_fast(self, tmp_path: Path) -> None:
        job = _job_with_analysis()
        artifact = Artifact.objects.create(job=job, type=ArtifactType.EPISODE_THUMBNAIL, index=0)
        with override_settings(MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")), patch(
            "services.graphic_renderer.render_thumbnail_to_png", side_effect=ImportError("no playwright")
        ), patch("workers.quote_graphic_worker.publish"):
            generate_thumbnail.apply_async(args=[str(artifact.id)])  # must not raise Retry
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "no playwright" in artifact.error

    def test_orchestrator_creates_thumbnail_slot(self) -> None:
        job = _job_with_analysis()
        job.status = JobStatus.ANALYZING
        job.save()
        with (
            patch("workers.video_clip_worker.generate_video_clip.apply_async"),
            patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"),
            patch("workers.text_artifact_worker.generate_twitter_thread.apply_async"),
            patch("workers.text_artifact_worker.generate_show_notes.apply_async"),
            patch("workers.text_artifact_worker.generate_newsletter.apply_async"),
            patch("workers.text_artifact_worker.generate_youtube_description.apply_async"),
            patch("workers.transcript_worker.generate_transcript.apply_async"),
            patch("workers.thumbnail_worker.generate_thumbnail.apply_async") as kick,
        ):
            orchestrate_artifacts.apply_async(args=[str(job.id)])
        assert Artifact.objects.filter(job=job, type=ArtifactType.EPISODE_THUMBNAIL).count() == 1
        assert kick.call_args.kwargs["queue"] == "graphics"

    def test_quote_worker_passes_branding(self, tmp_path: Path) -> None:
        from workers.quote_graphic_worker import generate_quote_graphic

        job = _job_with_analysis()
        Analysis.objects.filter(job=job).update(
            quotes_json=[{"text": "A quote that is definitely long enough", "speaker": "S", "ts_ms": 0}]
        )
        artifact = Artifact.objects.create(job=job, type=ArtifactType.QUOTE_GRAPHIC, index=0)
        seen: dict = {}

        def fake_render(quote, speaker, output_path, *, template_id, branding):
            seen["branding"] = branding
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(PNG_BYTES)

        with override_settings(MEDIA_ROOT=str(tmp_path), ARTIFACTS_ROOT=str(tmp_path / "artifacts")), patch(
            "services.graphic_renderer.render_quote_to_png", side_effect=fake_render
        ), patch("workers.quote_graphic_worker.publish"):
            generate_quote_graphic.apply_async(args=[str(artifact.id)])
        assert seen["branding"]["podcast_name"] == "Show"
