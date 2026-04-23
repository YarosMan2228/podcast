"""POST /api/jobs/upload — SPEC §§2.3, 2.5."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from api.errors import StorageError
from jobs.models import Job, JobStatus, SourceType

pytestmark = pytest.mark.django_db


UPLOAD_URL = "/api/jobs/upload"


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    """Redirect MEDIA_ROOT to a pytest tmp dir for the test duration."""
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _mp3_bytes(size: int = 64) -> bytes:
    # ID3v2 header + null padding is enough for the handler; we don't decode.
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * (size - 10)


def _upload_file(name: str = "ep01.mp3", content_type: str = "audio/mpeg", size: int = 64):
    return SimpleUploadedFile(name, _mp3_bytes(size), content_type=content_type)


# -------------------- Happy path --------------------


def test_upload_201_returns_job_id_and_pending(client, media_root):
    resp = client.post(UPLOAD_URL, {"file": _upload_file()}, format="multipart")

    assert resp.status_code == 201
    body = resp.json()
    # Parsing validates the returned job_id is a real UUID.
    job_id = uuid.UUID(body["job_id"])
    assert body["status"] == JobStatus.PENDING

    job = Job.objects.get(id=job_id)
    assert job.source_type == SourceType.FILE
    assert job.original_filename == "ep01.mp3"
    assert job.mime_type == "audio/mpeg"
    assert job.file_size_bytes == 64


def test_upload_writes_file_to_media_root(client, media_root):
    resp = client.post(UPLOAD_URL, {"file": _upload_file(size=128)}, format="multipart")

    job = Job.objects.get(id=resp.json()["job_id"])
    raw_path = Path(job.raw_media_path)

    # Path is under MEDIA_ROOT/uploads/<job_id>/<filename>.
    assert raw_path.is_file()
    assert raw_path.stat().st_size == 128
    assert raw_path.parent.name == str(job.id)
    assert raw_path.parent.parent == media_root / "uploads"


def test_upload_accepts_video_mime(client, media_root):
    resp = client.post(
        UPLOAD_URL,
        {"file": SimpleUploadedFile("ep.mp4", b"\x00" * 32, content_type="video/mp4")},
        format="multipart",
    )
    assert resp.status_code == 201
    assert Job.objects.get(id=resp.json()["job_id"]).mime_type == "video/mp4"


def test_upload_accepts_application_ogg(client, media_root):
    resp = client.post(
        UPLOAD_URL,
        {
            "file": SimpleUploadedFile(
                "ep.ogg", b"OggS" + b"\x00" * 60, content_type="application/ogg"
            )
        },
        format="multipart",
    )
    assert resp.status_code == 201


# -------------------- Error envelope --------------------


def test_no_file_returns_upload_no_file(client, media_root):
    resp = client.post(UPLOAD_URL, {}, format="multipart")

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "UPLOAD_NO_FILE"
    assert body["error"]["field"] == "file"
    assert Job.objects.count() == 0


def test_empty_file_returns_upload_empty_file(client, media_root):
    empty = SimpleUploadedFile("empty.mp3", b"", content_type="audio/mpeg")
    resp = client.post(UPLOAD_URL, {"file": empty}, format="multipart")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UPLOAD_EMPTY_FILE"
    assert resp.json()["error"]["field"] == "file"
    assert Job.objects.count() == 0


def test_wrong_mime_returns_invalid_format(client, media_root):
    pdf = SimpleUploadedFile("doc.pdf", b"%PDF-1.4\n", content_type="application/pdf")
    resp = client.post(UPLOAD_URL, {"file": pdf}, format="multipart")

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "UPLOAD_INVALID_FORMAT"
    assert body["error"]["field"] == "file"
    assert "application/pdf" in body["error"]["message"]
    assert Job.objects.count() == 0


@override_settings(MAX_UPLOAD_SIZE_BYTES=128, MAX_UPLOAD_SIZE_MB=1)
def test_oversize_returns_413(client, media_root):
    big = SimpleUploadedFile("big.mp3", b"\x00" * 256, content_type="audio/mpeg")
    resp = client.post(UPLOAD_URL, {"file": big}, format="multipart")

    assert resp.status_code == 413
    body = resp.json()
    assert body["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert body["error"]["field"] == "file"
    assert "1MB" in body["error"]["message"]
    assert Job.objects.count() == 0


def test_storage_failure_returns_500_and_creates_no_job(client, media_root):
    # Patch the view-side reference: `from pipeline.ingestion import save_upload`
    # creates `api.views.upload.save_upload`, which is what the request actually
    # calls. Patching `pipeline.ingestion.save_upload` wouldn't reach it.
    with patch(
        "api.views.upload.save_upload",
        side_effect=StorageError(message="disk on fire"),
    ):
        resp = client.post(
            UPLOAD_URL, {"file": _upload_file()}, format="multipart"
        )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "STORAGE_ERROR"
    assert Job.objects.count() == 0


def test_only_post_allowed(client, media_root):
    resp = client.get(UPLOAD_URL)
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


# -------------------- Security --------------------


def test_filename_traversal_is_sanitized(client, media_root):
    evil = SimpleUploadedFile(
        "../../../../etc/shadow", b"\x00" * 32, content_type="audio/mpeg"
    )
    resp = client.post(UPLOAD_URL, {"file": evil}, format="multipart")

    assert resp.status_code == 201
    job = Job.objects.get(id=resp.json()["job_id"])
    assert job.original_filename == "shadow"
    # Actual file lives strictly beneath MEDIA_ROOT/uploads/<job_id>/.
    raw = Path(job.raw_media_path).resolve()
    upload_dir = (media_root / "uploads" / str(job.id)).resolve()
    assert str(raw).startswith(str(upload_dir))


def test_backslash_traversal_is_sanitized(client, media_root):
    """Windows-style paths ('..\\..\\evil.mp3') also normalize to the basename."""
    evil = SimpleUploadedFile(
        "..\\..\\evil.mp3", b"\x00" * 16, content_type="audio/mpeg"
    )
    resp = client.post(UPLOAD_URL, {"file": evil}, format="multipart")

    assert resp.status_code == 201
    job = Job.objects.get(id=resp.json()["job_id"])
    assert ".." not in job.original_filename
    assert os.sep not in job.original_filename
    assert "/" not in job.original_filename


# -------------------- Streaming --------------------


def test_large_chunked_upload_streams_to_disk(client, media_root):
    """Files larger than FILE_UPLOAD_MAX_MEMORY_SIZE go through chunked write.

    Bytes delivered via ``.chunks()`` must be fully persisted — this covers
    the stream path in `_write_chunks` that pure in-memory tests skip.
    """
    # 11MB > default FILE_UPLOAD_MAX_MEMORY_SIZE (10MB) → Django uses
    # TemporaryFileUploadHandler and our handler consumes via .chunks().
    size = 11 * 1024 * 1024
    payload = b"A" * size
    f = SimpleUploadedFile("big.mp3", payload, content_type="audio/mpeg")

    resp = client.post(UPLOAD_URL, {"file": f}, format="multipart")
    assert resp.status_code == 201
    job = Job.objects.get(id=resp.json()["job_id"])
    assert job.file_size_bytes == size
    assert Path(job.raw_media_path).stat().st_size == size
