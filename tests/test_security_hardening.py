"""Security checklist regression tests (see docs/SECURITY.md).

Covers: access-token gate (#3 #5 #6 #10 #17 #19), throttles (#7), media
headers + traversal (#8 #14), logo content verification (#14), no key
material in error bodies (#1 #12), field tampering (#13).
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from api.errors import BrandingInvalid
from jobs.models import Artifact, ArtifactStatus, ArtifactType, Job, JobStatus, SourceType
from pipeline.branding import parse_branding

pytestmark = pytest.mark.django_db

TOKEN = "s3cret-demo-token"


def _png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 255, 0)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# ---------------------------------------------------------------------------
# Access token gate
# ---------------------------------------------------------------------------


class TestAccessTokenGate:
    def test_open_when_unset(self, client: APIClient) -> None:
        with override_settings(APP_ACCESS_TOKEN=""):
            assert client.get("/api/jobs").status_code == 200
            assert client.get("/api/auth/session").json() == {"required": False, "authenticated": True}

    def test_api_and_media_require_token(self, client: APIClient, tmp_path: Path) -> None:
        (tmp_path / "x.txt").write_text("hi")
        with override_settings(APP_ACCESS_TOKEN=TOKEN, MEDIA_ROOT=str(tmp_path)):
            res = client.get("/api/jobs")
            assert res.status_code == 401
            assert res.json()["error"]["code"] == "AUTH_REQUIRED"
            assert client.get("/media/x.txt").status_code == 401
            # Health + session probe stay reachable.
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/auth/session").json() == {"required": True, "authenticated": False}

    def test_header_cookie_and_query_are_accepted(self, client: APIClient, tmp_path: Path) -> None:
        (tmp_path / "x.txt").write_text("hi")
        with override_settings(APP_ACCESS_TOKEN=TOKEN, MEDIA_ROOT=str(tmp_path)):
            assert client.get("/api/jobs", HTTP_X_ACCESS_TOKEN=TOKEN).status_code == 200
            assert client.get("/api/jobs", HTTP_X_ACCESS_TOKEN="wrong").status_code == 401

            # ?token= works once and plants the cookie for <img>/<video>/downloads.
            res = client.get(f"/media/x.txt?token={TOKEN}")
            assert res.status_code == 200
            cookie = res.cookies["pp_access"]
            assert cookie.value == TOKEN
            assert cookie["httponly"] and cookie["samesite"] == "Lax"
            # Subsequent plain requests ride on the cookie.
            assert client.get("/media/x.txt").status_code == 200
            assert client.get("/api/jobs").status_code == 200

    def test_session_login_and_logout(self, client: APIClient) -> None:
        with override_settings(APP_ACCESS_TOKEN=TOKEN):
            bad = client.post("/api/auth/session", {"token": "nope"}, format="json")
            assert bad.status_code == 401
            assert bad.json()["error"]["code"] == "AUTH_INVALID"
            assert "pp_access" not in bad.cookies

            ok = client.post("/api/auth/session", {"token": TOKEN}, format="json")
            assert ok.status_code == 200 and ok.json() == {"authenticated": True}
            assert client.get("/api/auth/session").json() == {"required": True, "authenticated": True}
            assert client.get("/api/jobs").status_code == 200

            client.delete("/api/auth/session")
            assert client.get("/api/jobs").status_code == 401

    def test_gate_protects_job_records(self, client: APIClient) -> None:
        """IDOR-style probe: knowing a UUID is not enough without the token."""
        job = Job.objects.create(source_type=SourceType.FILE)
        with override_settings(APP_ACCESS_TOKEN=TOKEN):
            assert client.get(f"/api/jobs/{job.id}").status_code == 401
            assert client.delete(f"/api/jobs/{job.id}").status_code == 401
            assert Job.objects.filter(id=job.id).exists()


# ---------------------------------------------------------------------------
# Throttles
# ---------------------------------------------------------------------------


class TestThrottles:
    def test_upload_throttle_returns_429(self, client: APIClient, tmp_path: Path) -> None:
        with override_settings(UPLOAD_RATE_LIMIT="2/hour", MEDIA_ROOT=str(tmp_path)), patch(
            "api.views.upload.start_job.apply_async"
        ):
            codes = []
            for _ in range(3):
                f = SimpleUploadedFile("ep.mp3", b"\x00" * 64, content_type="audio/mpeg")
                codes.append(client.post("/api/jobs/upload", {"file": f}, format="multipart").status_code)
        assert codes == [201, 201, 429]

    def test_from_url_shares_the_upload_bucket(self, client: APIClient) -> None:
        with override_settings(UPLOAD_RATE_LIMIT="1/hour"), patch("api.views.upload.start_job.apply_async"):
            assert client.post("/api/jobs/from_url", {"url": "https://youtu.be/a"}, format="json").status_code == 201
            res = client.post("/api/jobs/from_url", {"url": "https://youtu.be/b"}, format="json")
        assert res.status_code == 429
        assert "Retry-After" in res

    def test_global_api_throttle(self, client: APIClient) -> None:
        with override_settings(API_RATE_LIMIT="3/min"):
            codes = [client.get("/api/jobs").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]


# ---------------------------------------------------------------------------
# Media hardening
# ---------------------------------------------------------------------------


class TestMediaHardening:
    def test_media_headers_forbid_sniffing_and_scripts(self, client: APIClient, tmp_path: Path) -> None:
        (tmp_path / "evil.html").write_text("<script>alert(1)</script>")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            res = client.get("/media/evil.html")
        assert res.status_code == 200
        assert res["X-Content-Type-Options"] == "nosniff"
        assert res["Content-Security-Policy"].startswith("sandbox")

    def test_media_path_traversal_is_blocked(self, client: APIClient, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("top")
        media = tmp_path / "media"
        media.mkdir()
        with override_settings(MEDIA_ROOT=str(media)):
            for probe in ("/media/../secret.txt", "/media/..%2Fsecret.txt", "/media/%2e%2e/secret.txt"):
                res = client.get(probe)
                assert res.status_code in (400, 404), probe
                assert b"top" not in getattr(res, "content", b"")


# ---------------------------------------------------------------------------
# Upload content verification
# ---------------------------------------------------------------------------


class TestLogoVerification:
    def test_real_png_and_jpeg_accepted_whatever_the_header_says(self) -> None:
        png = SimpleUploadedFile("logo.bin", _png_bytes(), content_type="application/octet-stream")
        assert parse_branding({}, {"logo": png}).logo.content_type == "image/png"
        jpg = SimpleUploadedFile("logo.png", _jpeg_bytes(), content_type="image/png")  # lying header
        assert parse_branding({}, {"logo": jpg}).logo.content_type == "image/jpeg"

    def test_svg_is_rejected_even_with_image_mime(self) -> None:
        svg = SimpleUploadedFile("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", content_type="image/svg+xml")
        with pytest.raises(BrandingInvalid) as exc:
            parse_branding({}, {"logo": svg})
        assert exc.value.field == "logo"

    def test_html_disguised_as_png_is_rejected(self) -> None:
        fake = SimpleUploadedFile("logo.png", b"<html><script>alert(1)</script></html>", content_type="image/png")
        with pytest.raises(BrandingInvalid):
            parse_branding({}, {"logo": fake})

    def test_upload_endpoint_rejects_fake_logo(self, client: APIClient, tmp_path: Path) -> None:
        f = SimpleUploadedFile("ep.mp3", b"\x00" * 64, content_type="audio/mpeg")
        fake = SimpleUploadedFile("logo.png", b"not an image", content_type="image/png")
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            res = client.post("/api/jobs/upload", {"file": f, "logo": fake}, format="multipart")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "BRANDING_INVALID"
        assert Job.objects.count() == 0


# ---------------------------------------------------------------------------
# No secrets in responses / field tampering
# ---------------------------------------------------------------------------


class TestNoSecretLeaks:
    def test_preflight_503_does_not_echo_key_material(self, client: APIClient) -> None:
        with override_settings(OPENAI_API_KEY="sk-placeholder-ABCDEFG", ANTHROPIC_API_KEY="sk-ant-real-looking-key-XYZ"):
            f = SimpleUploadedFile("ep.mp3", b"\x00" * 64, content_type="audio/mpeg")
            res = client.post("/api/jobs/upload", {"file": f}, format="multipart")
        assert res.status_code == 503
        msg = res.json()["error"]["message"]
        assert "ABCDEFG" not in msg and "sk-place" not in msg and "XYZ" not in msg

    def test_job_payload_has_no_filesystem_paths(self, client: APIClient) -> None:
        job = Job.objects.create(
            source_type=SourceType.FILE, raw_media_path="/app/media/uploads/x/raw.mp3",
            normalized_wav_path="/app/media/uploads/x/normalized.wav", package_path="packages/p.zip",
        )
        body = client.get(f"/api/jobs/{job.id}").content.decode()
        assert "/app/media/uploads" not in body
        assert "normalized.wav" not in body
        for row in client.get("/api/jobs").json()["jobs"]:
            assert "raw_media_path" not in row and "normalized_wav_path" not in row

    def test_regenerate_ignores_tampered_fields(self, client: APIClient) -> None:
        job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.COMPLETED)
        artifact = Artifact.objects.create(
            job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY, version=1,
            metadata_json={"tone": "casual"},
        )
        with patch("workers.text_artifact_worker.generate_linkedin_post.apply_async"):
            res = client.post(
                f"/api/artifacts/{artifact.id}/regenerate",
                {"version": 99, "status": "READY", "metadata_json": {"tone": "hacked"}, "job_id": "other"},
                format="json",
            )
        assert res.status_code == 202
        artifact.refresh_from_db()
        assert artifact.version == 2
        assert artifact.status == ArtifactStatus.QUEUED
        assert artifact.metadata_json["tone"] == "casual"
        assert artifact.job_id == job.id
