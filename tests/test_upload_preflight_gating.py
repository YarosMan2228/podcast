"""Upload views must reject 503 SERVICE_NOT_CONFIGURED before persisting."""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from jobs.models import Job
from services import preflight

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    preflight.reset_cache()
    yield
    preflight.reset_cache()


def _wav_bytes() -> bytes:
    """A 44-byte RIFF/WAVE header — minimal valid file for the mime gate."""
    return (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        b"\x01\x00\x01\x00\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00"
        b"data\x00\x00\x00\x00"
    )


# ---------------------------------------------------------------------------
# /api/jobs/upload
# ---------------------------------------------------------------------------


@override_settings(OPENAI_API_KEY="sk-placeholder", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_upload_returns_503_when_openai_key_is_placeholder() -> None:
    client = Client()
    res = client.post(
        "/api/jobs/upload",
        data={"file": ("ep.wav", io.BytesIO(_wav_bytes()), "audio/wav")},
        format="multipart",
    )
    assert res.status_code == 503
    body = res.json()
    assert body["error"]["code"] == "SERVICE_NOT_CONFIGURED"
    assert "OPENAI_API_KEY" in body["error"]["message"]


@override_settings(OPENAI_API_KEY="sk-placeholder", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_upload_does_not_create_job_row_when_gated() -> None:
    """A 503 must not leave a stub Job behind — otherwise the failed-job
    list grows by one per failed upload attempt."""
    client = Client()
    Job.objects.all().delete()
    client.post(
        "/api/jobs/upload",
        data={"file": ("ep.wav", io.BytesIO(_wav_bytes()), "audio/wav")},
        format="multipart",
    )
    assert Job.objects.count() == 0


@override_settings(OPENAI_API_KEY="sk-real-aaaa", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_upload_passes_preflight_with_real_keys() -> None:
    """When keys look real, upload reaches the file-validation step
    (we then short-circuit by uploading an unsupported mime to avoid
    invoking the actual ingestion task)."""
    client = Client()
    res = client.post(
        "/api/jobs/upload",
        data={"file": ("ep.txt", io.BytesIO(b"not media"), "text/plain")},
        format="multipart",
    )
    # 400 UPLOAD_INVALID_FORMAT — proves we passed the preflight gate.
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "UPLOAD_INVALID_FORMAT"


# ---------------------------------------------------------------------------
# /api/jobs/from_url
# ---------------------------------------------------------------------------


@override_settings(OPENAI_API_KEY="sk-placeholder", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_from_url_returns_503_when_keys_are_bad() -> None:
    client = Client()
    res = client.post(
        "/api/jobs/from_url",
        data={"url": "https://youtube.com/watch?v=abc"},
        content_type="application/json",
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "SERVICE_NOT_CONFIGURED"


@override_settings(OPENAI_API_KEY="sk-placeholder", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_from_url_does_not_create_job_when_gated() -> None:
    client = Client()
    Job.objects.all().delete()
    client.post(
        "/api/jobs/from_url",
        data={"url": "https://youtube.com/watch?v=abc"},
        content_type="application/json",
    )
    assert Job.objects.count() == 0


# ---------------------------------------------------------------------------
# Both keys missing — both names appear in the message
# ---------------------------------------------------------------------------


@override_settings(OPENAI_API_KEY="", ANTHROPIC_API_KEY="")
def test_both_missing_keys_listed_in_one_message() -> None:
    client = Client()
    res = client.post(
        "/api/jobs/from_url",
        data={"url": "https://youtube.com/watch?v=abc"},
        content_type="application/json",
    )
    assert res.status_code == 503
    msg = res.json()["error"]["message"]
    assert "OPENAI_API_KEY" in msg
    assert "ANTHROPIC_API_KEY" in msg


# ---------------------------------------------------------------------------
# Network-probe failure also gates uploads (cached)
# ---------------------------------------------------------------------------


@override_settings(OPENAI_API_KEY="sk-real-aaaa", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_upload_does_not_run_network_probes() -> None:
    """Hot upload path must stay structural-only so we don't pay a 100ms
    OpenAI ping on every request."""
    with patch("services.preflight._probe_openai") as oai, patch(
        "services.preflight._probe_anthropic"
    ) as ant:
        client = Client()
        client.post(
            "/api/jobs/upload",
            data={"file": ("ep.txt", io.BytesIO(b"not media"), "text/plain")},
            format="multipart",
        )
    oai.assert_not_called()
    ant.assert_not_called()
