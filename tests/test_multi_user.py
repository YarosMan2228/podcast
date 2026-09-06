"""Multi-user isolation (docs/SECURITY.md #2 #3 #6 #19).

APP_MULTI_USER=1: per-user keys, jobs owned, everything scoped. Strangers
get 404 (never 403) so ids aren't confirmed. Master token sees all.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from jobs.models import AccessKey, Artifact, ArtifactStatus, ArtifactType, Job, JobStatus, SourceType
from services.access import create_access_key, hash_key, resolve_principal

pytestmark = pytest.mark.django_db

MASTER = "master-token-xyz"
MU = dict(APP_MULTI_USER=True, APP_ACCESS_TOKEN=MASTER)


@pytest.fixture
def alice() -> tuple[AccessKey, str]:
    return create_access_key("Alice")


@pytest.fixture
def bob() -> tuple[AccessKey, str]:
    return create_access_key("Bob")


def _client(secret: str | None = None) -> APIClient:
    c = APIClient()
    if secret:
        c.credentials(HTTP_X_ACCESS_TOKEN=secret)
    return c


def _job(owner: AccessKey | None, **kw) -> Job:
    return Job.objects.create(source_type=SourceType.FILE, owner=owner, **kw)


class TestKeys:
    def test_secret_is_hashed_and_shown_once(self, alice) -> None:
        row, raw = alice
        assert raw.startswith("pk_") and len(raw) > 30
        assert row.key_hash == hash_key(raw)
        assert raw not in row.key_hash

    def test_resolve_modes(self, alice) -> None:
        row, raw = alice
        with override_settings(APP_MULTI_USER=False, APP_ACCESS_TOKEN=MASTER):
            assert resolve_principal(raw) is None  # keys ignored outside multi-user
            assert resolve_principal(MASTER).is_admin
        with override_settings(**MU):
            p = resolve_principal(raw)
            assert p.key.id == row.id and not p.is_admin and p.name == "Alice"
            assert resolve_principal("pk_nope") is None
            AccessKey.objects.filter(id=row.id).update(is_active=False)
            assert resolve_principal(raw) is None

    def test_management_command(self) -> None:
        out = StringIO()
        with override_settings(**MU):
            call_command("access_key", "create", "--name", "Carol", stdout=out)
            raw = [l.strip() for l in out.getvalue().splitlines() if l.strip().startswith("pk_")][0]
            assert resolve_principal(raw).name == "Carol"
            out = StringIO()
            call_command("access_key", "list", stdout=out)
            assert "Carol" in out.getvalue()
            call_command("access_key", "revoke", "Carol", stdout=StringIO())
            assert resolve_principal(raw) is None


class TestIsolation:
    def test_jobs_are_owned_on_create(self, alice, tmp_path: Path) -> None:
        _, raw = alice
        with override_settings(MEDIA_ROOT=str(tmp_path), **MU), patch("api.views.upload.start_job.apply_async"):
            c = _client(raw)
            f = SimpleUploadedFile("ep.mp3", b"\x00" * 64, content_type="audio/mpeg")
            r1 = c.post("/api/jobs/upload", {"file": f}, format="multipart")
            r2 = c.post("/api/jobs/from_url", {"url": "https://youtu.be/a"}, format="json")
        assert r1.status_code == 201 and r2.status_code == 201
        assert {Job.objects.get(id=r["job_id"]).owner.name for r in (r1.json(), r2.json())} == {"Alice"}

    def test_list_is_scoped(self, alice, bob) -> None:
        a, raw_a = alice
        b, raw_b = bob
        _job(a); _job(a); _job(b); _job(None)
        with override_settings(**MU):
            assert len(_client(raw_a).get("/api/jobs").json()["jobs"]) == 2
            assert len(_client(raw_b).get("/api/jobs").json()["jobs"]) == 1
            assert len(_client(MASTER).get("/api/jobs").json()["jobs"]) == 4
            assert _client().get("/api/jobs").status_code == 401

    def test_foreign_job_is_404_everywhere(self, alice, bob, tmp_path: Path) -> None:
        a, raw_a = alice
        b, raw_b = bob
        job = _job(a, status=JobStatus.COMPLETED, package_path="packages/podcast_pack_x.zip")
        art = Artifact.objects.create(job=job, type=ArtifactType.LINKEDIN_POST, index=0, status=ArtifactStatus.READY)
        (tmp_path / "packages").mkdir()
        (tmp_path / "packages" / "podcast_pack_x.zip").write_bytes(b"PK")
        with override_settings(MEDIA_ROOT=str(tmp_path), **MU), patch(
            "workers.text_artifact_worker.generate_linkedin_post.apply_async"
        ):
            stranger = _client(raw_b)
            assert stranger.get(f"/api/jobs/{job.id}").status_code == 404
            assert stranger.delete(f"/api/jobs/{job.id}").status_code == 404
            assert stranger.get(f"/api/jobs/{job.id}/download").status_code == 404
            assert stranger.get(f"/api/jobs/{job.id}/download?part=text").status_code == 404
            assert stranger.get(f"/api/jobs/{job.id}/events").status_code == 404
            assert stranger.post(f"/api/artifacts/{art.id}/regenerate", {}, format="json").status_code == 404
            assert Job.objects.filter(id=job.id).exists()

            owner = _client(raw_a)
            assert owner.get(f"/api/jobs/{job.id}").status_code == 200
            assert owner.get(f"/api/jobs/{job.id}/download").status_code == 200
            assert owner.post(f"/api/artifacts/{art.id}/regenerate", {}, format="json").status_code == 202
            assert _client(MASTER).get(f"/api/jobs/{job.id}").status_code == 200

    def test_media_is_scoped_by_job_folder(self, alice, bob, tmp_path: Path) -> None:
        a, raw_a = alice
        b, raw_b = bob
        job = _job(a, package_path="packages/podcast_pack_ab.zip")
        for rel in (f"artifacts/{job.id}/clip_0_v1.mp4", f"uploads/{job.id}/logo.png", "packages/podcast_pack_ab.zip", "stray.txt"):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        with override_settings(MEDIA_ROOT=str(tmp_path), **MU):
            owner, stranger, admin = _client(raw_a), _client(raw_b), _client(MASTER)
            for rel in (f"artifacts/{job.id}/clip_0_v1.mp4", f"uploads/{job.id}/logo.png", "packages/podcast_pack_ab.zip"):
                assert owner.get(f"/media/{rel}").status_code == 200, rel
                assert stranger.get(f"/media/{rel}").status_code == 404, rel
                assert admin.get(f"/media/{rel}").status_code == 200, rel
            # Files outside a job folder: admin only.
            assert owner.get("/media/stray.txt").status_code == 404
            assert admin.get("/media/stray.txt").status_code == 200
            assert owner.get("/media/artifacts/not-a-uuid/x.mp4").status_code == 404

    def test_session_reports_identity(self, alice) -> None:
        _, raw = alice
        with override_settings(**MU):
            c = APIClient()
            assert c.get("/api/auth/session").json() == {"required": True, "authenticated": False, "name": None, "is_admin": False}
            r = c.post("/api/auth/session", {"token": raw}, format="json")
            assert r.json() == {"required": True, "authenticated": True, "name": "Alice", "is_admin": False}
            assert c.get("/api/auth/session").json()["name"] == "Alice"  # via cookie
            assert c.post("/api/auth/session", {"token": MASTER}, format="json").json()["is_admin"] is True

    def test_open_mode_unchanged(self) -> None:
        _job(None)
        with override_settings(APP_MULTI_USER=False, APP_ACCESS_TOKEN=""):
            assert len(_client().get("/api/jobs").json()["jobs"]) == 1
