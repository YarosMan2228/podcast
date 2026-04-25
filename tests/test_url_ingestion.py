"""POST /api/jobs/from_url + url_ingestion module — SPEC §§2.3, 2.4, 2.5."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from jobs.models import Job, JobStatus, SourceType
from pipeline.ingestion import IngestionError
from pipeline.url_ingestion import (
    UnsupportedHostError,
    UrlValidationError,
    download_from_url,
    validate_url,
)

pytestmark = pytest.mark.django_db


URL = "/api/jobs/from_url"
SAMPLE_YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _stub_dispatch():
    """Don't actually enqueue the Celery chain in unit tests."""
    with patch("workers.tasks.start_job.apply_async") as mock:
        yield mock


# -------------------- validate_url --------------------


def test_validate_url_accepts_youtube_full_host():
    assert validate_url(SAMPLE_YT) == SAMPLE_YT


def test_validate_url_accepts_youtu_be_short():
    assert validate_url("https://youtu.be/dQw4w9WgXcQ").endswith("dQw4w9WgXcQ")


def test_validate_url_accepts_mobile_youtube():
    assert validate_url("https://m.youtube.com/watch?v=abc")


def test_validate_url_rejects_empty():
    with pytest.raises(UrlValidationError):
        validate_url("")


def test_validate_url_rejects_none():
    with pytest.raises(UrlValidationError):
        validate_url(None)


def test_validate_url_rejects_non_http_scheme():
    with pytest.raises(UrlValidationError):
        validate_url("ftp://youtube.com/watch?v=x")


def test_validate_url_rejects_spotify():
    with pytest.raises(UnsupportedHostError) as ei:
        validate_url("https://open.spotify.com/episode/abc")
    assert "spotify" in ei.value.host


def test_validate_url_rejects_random_host():
    with pytest.raises(UnsupportedHostError) as ei:
        validate_url("https://example.com/video.mp4")
    assert ei.value.host == "example.com"


# -------------------- POST /api/jobs/from_url --------------------


def test_from_url_201_creates_job(client, _stub_dispatch):
    res = client.post(URL, {"url": SAMPLE_YT}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    job = Job.objects.get(id=body["job_id"])
    assert job.source_type == SourceType.URL
    assert job.source_url == SAMPLE_YT
    assert job.status == JobStatus.PENDING
    assert _stub_dispatch.called


def test_from_url_dispatches_start_job(client, _stub_dispatch):
    client.post(URL, {"url": SAMPLE_YT}, format="json")
    args = _stub_dispatch.call_args.kwargs.get("args") or _stub_dispatch.call_args.args[0]
    # apply_async(args=[job_id]) is the contract — accept either calling style.
    if isinstance(args, dict):
        args = args["args"]
    assert len(args) == 1


def test_from_url_400_url_invalid_when_missing(client):
    res = client.post(URL, {}, format="json")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "URL_INVALID"


def test_from_url_400_url_invalid_when_non_http(client):
    res = client.post(URL, {"url": "not-a-url"}, format="json")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "URL_INVALID"


def test_from_url_400_url_unsupported_host_for_spotify(client):
    res = client.post(URL, {"url": "https://open.spotify.com/episode/x"}, format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "URL_UNSUPPORTED_HOST"
    assert "spotify" in body["error"]["message"]


def test_from_url_only_post_allowed(client):
    res = client.get(URL)
    assert res.status_code == 405


# -------------------- download_from_url --------------------


def test_download_from_url_calls_ytdlp_with_correct_options(tmp_path):
    """Verify yt-dlp is invoked with audio-only mp3 postprocessor (SPEC §2.4)."""
    captured: dict = {}

    class FakeYDL:
        def __init__(self, options):
            captured["options"] = options
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def download(self, urls):
            captured["urls"] = list(urls)
            # Simulate yt-dlp's behaviour of writing the postprocessed mp3.
            (tmp_path / "raw.mp3").write_bytes(b"fake mp3 bytes")

    with patch("yt_dlp.YoutubeDL", FakeYDL):
        out = download_from_url(SAMPLE_YT, tmp_path)

    assert out == tmp_path / "raw.mp3"
    assert captured["urls"] == [SAMPLE_YT]
    pps = captured["options"]["postprocessors"]
    assert any(
        p.get("key") == "FFmpegExtractAudio" and p.get("preferredcodec") == "mp3"
        for p in pps
    )


def test_download_from_url_raises_ingestion_error_on_download_error(tmp_path):
    from yt_dlp.utils import DownloadError

    class FailingYDL:
        def __init__(self, options): pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def download(self, urls):
            raise DownloadError("This is a live stream")

    with patch("yt_dlp.YoutubeDL", FailingYDL):
        with pytest.raises(IngestionError) as ei:
            download_from_url(SAMPLE_YT, tmp_path)

    assert ei.value.code == "URL_YTDLP_FAILED"
    assert "live stream" in ei.value.message


def test_download_from_url_raises_when_no_output_file(tmp_path):
    """yt-dlp claimed success but produced no file — surface as URL_YTDLP_FAILED."""

    class SilentYDL:
        def __init__(self, options): pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def download(self, urls):
            pass  # no file written

    with patch("yt_dlp.YoutubeDL", SilentYDL):
        with pytest.raises(IngestionError) as ei:
            download_from_url(SAMPLE_YT, tmp_path)
    assert ei.value.code == "URL_YTDLP_FAILED"


# -------------------- ingest_job integration with URL source --------------------


def test_ingest_job_downloads_url_then_normalizes(tmp_path, settings):
    """ingest_job should pull the URL, then run normalize + probe."""
    from pipeline.ingestion import ingest_job

    settings.MEDIA_ROOT = str(tmp_path)

    job = Job.objects.create(
        source_type=SourceType.URL,
        source_url=SAMPLE_YT,
        status=JobStatus.INGESTING,
    )

    download_target = tmp_path / "uploads" / str(job.id) / "raw.mp3"

    def fake_download(url, dest_dir, **_kw):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        download_target.write_bytes(b"\x00" * 4096)
        return download_target

    def fake_normalize(input_path, output_path, **_):
        Path(output_path).write_bytes(b"\x00" * 4096)

    def fake_probe(path, **_):
        return 60.0

    with patch("pipeline.url_ingestion.download_from_url", side_effect=fake_download), \
         patch("pipeline.ingestion.normalize_to_wav", side_effect=fake_normalize), \
         patch("pipeline.ingestion.probe_duration_sec", side_effect=fake_probe):
        ingest_job(str(job.id))

    job.refresh_from_db()
    assert job.raw_media_path == str(download_target)
    assert job.normalized_wav_path is not None
    assert job.duration_sec == 60.0
