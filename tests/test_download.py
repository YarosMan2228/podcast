"""GET /api/jobs/:id/download — SPEC §9.3."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from jobs.models import Job, JobStatus, SourceType

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/download"


def _make_zip(path: Path, body_name: str = "index.txt", body: bytes = b"hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(body_name, body)


def _make_job(
    media_root: Path,
    *,
    status: str,
    package_rel: str | None,
) -> Job:
    raw = media_root / "uploads" / "ep.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"\x00" * 16)
    return Job.objects.create(
        source_type=SourceType.FILE,
        raw_media_path=str(raw),
        status=status,
        package_path=package_rel,
    )


# -------------------- happy path --------------------


def test_download_streams_zip_for_completed_job(client, tmp_path):
    rel = "packages/podcast_pack_abc.zip"
    abs_path = tmp_path / rel
    _make_zip(abs_path, body=b"index.txt body")

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        job = _make_job(tmp_path, status=JobStatus.COMPLETED, package_rel=rel)
        res = client.get(_url(str(job.id)))

    assert res.status_code == 200
    assert res["Content-Type"] == "application/zip"
    assert "podcast_pack_abc.zip" in res["Content-Disposition"]
    body = b"".join(res.streaming_content)
    assert body == abs_path.read_bytes()
    assert int(res["Content-Length"]) == len(body)


# -------------------- 404 cases --------------------


def test_download_returns_404_when_job_unknown(client):
    res = client.get(_url("00000000-0000-0000-0000-000000000000"))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_download_returns_404_when_job_id_not_uuid(client):
    res = client.get(_url("not-a-uuid"))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.parametrize("status", [
    JobStatus.PENDING, JobStatus.INGESTING, JobStatus.GENERATING,
    JobStatus.PACKAGING, JobStatus.FAILED,
])
def test_download_returns_package_not_ready_when_not_completed(client, tmp_path, status):
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        job = _make_job(tmp_path, status=status, package_rel=None)
        res = client.get(_url(str(job.id)))
    assert res.status_code == 404
    body = res.json()
    assert body["error"]["code"] == "PACKAGE_NOT_READY"
    assert status in body["error"]["message"]


def test_download_returns_package_not_ready_when_file_missing(client, tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        job = _make_job(
            tmp_path,
            status=JobStatus.COMPLETED,
            package_rel="packages/missing.zip",
        )
        res = client.get(_url(str(job.id)))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PACKAGE_NOT_READY"


# -------------------- _serialize_job exposes package_url --------------------


def test_get_job_returns_real_package_url_when_completed(client, tmp_path):
    rel = "packages/podcast_pack_xyz.zip"
    _make_zip(tmp_path / rel)
    with override_settings(MEDIA_ROOT=str(tmp_path), MEDIA_URL="/media/"):
        job = _make_job(tmp_path, status=JobStatus.COMPLETED, package_rel=rel)
        res = client.get(f"/api/jobs/{job.id}")
    assert res.status_code == 200
    assert res.json()["package_url"] == "/media/packages/podcast_pack_xyz.zip"


def test_get_job_package_url_is_null_before_completion(client, tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path), MEDIA_URL="/media/"):
        job = _make_job(tmp_path, status=JobStatus.GENERATING, package_rel=None)
        res = client.get(f"/api/jobs/{job.id}")
    assert res.status_code == 200
    assert res.json()["package_url"] is None
