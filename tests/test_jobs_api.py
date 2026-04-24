"""GET /api/jobs/:id + SSE /api/jobs/:id/events (SPEC §9.3).

The REST view is exercised through DRF's APIClient; the SSE generator is
exercised against a stub PubSub so we can assert the exact byte stream
without needing a Redis process.
"""
from __future__ import annotations

import json
import queue
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from api.views.jobs import _format_sse, _sse_stream
from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# ---------------------------------------------------------------------------
# GET /api/jobs/:id  (SPEC §9.3)
# ---------------------------------------------------------------------------


def _make_job(**kwargs) -> Job:
    defaults = {"source_type": SourceType.FILE}
    defaults.update(kwargs)
    return Job.objects.create(**defaults)


def _make_analysis(job: Job) -> Analysis:
    return Analysis.objects.create(
        job=job,
        episode_title="Hidden Cost of AI Hype",
        hook="Most AI startups are building on sand.",
        themes_json=["ai"],
        chapters_json=[],
        clip_candidates_json=[],
        quotes_json=[],
        claude_model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
    )


def test_get_job_returns_full_state(client: APIClient) -> None:
    job = _make_job(status=JobStatus.GENERATING)
    _make_analysis(job)
    Artifact.objects.create(
        job=job,
        type=ArtifactType.VIDEO_CLIP,
        index=0,
        status=ArtifactStatus.READY,
        file_path=f"artifacts/{job.id}/clip_0_v1.mp4",
        metadata_json={"virality_score": 9, "duration_sec": 55.0},
    )
    Artifact.objects.create(
        job=job,
        type=ArtifactType.VIDEO_CLIP,
        index=1,
        status=ArtifactStatus.PROCESSING,
    )

    res = client.get(f"/api/jobs/{job.id}")
    assert res.status_code == 200
    body = res.json()

    # Top-level envelope per SPEC §9.3.
    assert body["job_id"] == str(job.id)
    assert body["status"] == JobStatus.GENERATING
    assert body["analysis"] == {
        "episode_title": "Hidden Cost of AI Hype",
        "hook": "Most AI startups are building on sand.",
    }
    assert body["package_url"] is None
    assert body["error"] is None

    # Progress counters match the two artifact rows we created.
    assert body["progress"] == {
        "total_artifacts": 2,
        "ready": 1,
        "processing": 1,
        "queued": 0,
        "failed": 0,
    }

    # Artifacts ordered by (type, index) → both VIDEO_CLIP, index 0 then 1.
    assert [a["index"] for a in body["artifacts"]] == [0, 1]
    ready = body["artifacts"][0]
    assert ready["status"] == ArtifactStatus.READY
    assert ready["file_url"] == f"/media/artifacts/{job.id}/clip_0_v1.mp4"
    assert ready["metadata"] == {"virality_score": 9, "duration_sec": 55.0}
    assert ready["version"] == 1
    assert body["artifacts"][1]["file_url"] is None  # not ready yet


def test_get_job_without_analysis_returns_null_block(client: APIClient) -> None:
    job = _make_job(status=JobStatus.TRANSCRIBING)
    res = client.get(f"/api/jobs/{job.id}")
    assert res.status_code == 200
    assert res.json()["analysis"] is None
    assert res.json()["artifacts"] == []
    assert res.json()["progress"]["total_artifacts"] == 0


def test_get_job_surfaces_error_field(client: APIClient) -> None:
    job = _make_job(status=JobStatus.FAILED, error="ANALYSIS_INVALID_JSON: bad")
    res = client.get(f"/api/jobs/{job.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == JobStatus.FAILED
    assert body["error"] == "ANALYSIS_INVALID_JSON: bad"


def test_get_job_missing_returns_structured_404(client: APIClient) -> None:
    job_id = uuid.uuid4()
    res = client.get(f"/api/jobs/{job_id}")
    assert res.status_code == 404
    body = res.json()
    assert body["error"]["code"] == "JOB_NOT_FOUND"
    assert body["error"]["field"] == "job_id"
    assert str(job_id) in body["error"]["message"]


def test_get_job_invalid_uuid_returns_404_not_500(client: APIClient) -> None:
    """A garbage id must 404 cleanly, not 500 via the DB layer."""
    res = client.get("/api/jobs/not-a-uuid")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOB_NOT_FOUND"


@override_settings(MEDIA_URL="/assets/")
def test_get_job_respects_custom_media_url(client: APIClient) -> None:
    job = _make_job(status=JobStatus.GENERATING)
    Artifact.objects.create(
        job=job,
        type=ArtifactType.VIDEO_CLIP,
        index=0,
        status=ArtifactStatus.READY,
        file_path=f"artifacts/{job.id}/clip_0_v1.mp4",
    )
    res = client.get(f"/api/jobs/{job.id}")
    assert res.json()["artifacts"][0]["file_url"].startswith("/assets/artifacts/")


# ---------------------------------------------------------------------------
# SSE helpers — generator + wire format
# ---------------------------------------------------------------------------


class FakePubSub:
    """Minimal stand-in for redis.client.PubSub used by _sse_stream.

    Feed messages via ``.push(event, data)``; ``get_message`` returns them
    in order and then returns ``None`` once the queue is drained so the
    stream emits a keepalive. Call ``.close_stream()`` to make the next
    ``get_message`` raise ``StopIteration`` (via a sentinel).
    """

    _STOP = object()

    def __init__(self) -> None:
        self._q: "queue.SimpleQueue[object]" = queue.SimpleQueue()
        self.unsubscribed = False
        self.closed = False

    # API surface the generator uses ------------------------------------
    def get_message(self, *, timeout: float, ignore_subscribe_messages: bool):
        try:
            item = self._q.get_nowait()
        except queue.Empty:
            return None
        if item is self._STOP:
            raise GeneratorExit  # drives the stream's finally block
        return item

    def unsubscribe(self) -> None:
        self.unsubscribed = True

    def close(self) -> None:
        self.closed = True

    # Test helpers ------------------------------------------------------
    def push(self, event: str, data: dict) -> None:
        payload = json.dumps({"event": event, "data": data}).encode("utf-8")
        self._q.put({"type": "message", "data": payload})

    def push_raw(self, blob: bytes | str) -> None:
        self._q.put({"type": "message", "data": blob})

    def stop(self) -> None:
        self._q.put(self._STOP)


def _collect(stream, max_frames: int = 50) -> list[bytes]:
    """Drain an SSE generator up to a cap (safeguard against infinite loops)."""
    frames: list[bytes] = []
    try:
        for i, chunk in enumerate(stream):
            frames.append(chunk)
            if i >= max_frames:
                break
    except GeneratorExit:
        pass
    return frames


def test_format_sse_wire_shape() -> None:
    out = _format_sse("artifact_ready", {"artifact_id": "abc", "index": 2})
    text = out.decode()
    assert text.startswith("event: artifact_ready\n")
    assert "data: " in text
    # JSON must round-trip exactly.
    data_line = [ln for ln in text.splitlines() if ln.startswith("data: ")][0]
    assert json.loads(data_line[len("data: "):]) == {"artifact_id": "abc", "index": 2}
    # Double-newline terminator required by EventSource parsers.
    assert text.endswith("\n\n")


def test_sse_stream_emits_connected_preamble_and_events() -> None:
    ps = FakePubSub()
    ps.push("status_changed", {"status": "ANALYZING"})
    ps.push("artifact_ready", {"artifact_id": "a1", "index": 0})
    ps.stop()

    frames = _collect(_sse_stream("job-1", ps, keepalive_sec=0.01))

    # First frame is the connection-open comment per EventSource spec.
    assert frames[0] == b": connected\n\n"
    # Events arrive in order.
    assert b"event: status_changed" in frames[1]
    assert b"event: artifact_ready" in frames[2]
    # Generator's finally cleaned up the subscription.
    assert ps.unsubscribed and ps.closed


def test_sse_stream_emits_keepalive_on_idle() -> None:
    """No messages in the buffer → yields ``: keepalive`` and loops."""
    ps = FakePubSub()
    # Immediately stop after one keepalive so we don't spin.

    def _stop_after_first_call() -> None:
        ps.stop()

    original = ps.get_message
    call_count = {"n": 0}

    def counting_get_message(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # triggers keepalive branch
        return original(**kwargs)

    ps.get_message = counting_get_message  # type: ignore[assignment]
    ps.stop()

    frames = _collect(_sse_stream("job-1", ps, keepalive_sec=0.01))
    assert b": connected\n\n" in frames
    assert b": keepalive\n\n" in frames


def test_sse_stream_skips_non_json_payloads() -> None:
    ps = FakePubSub()
    ps.push_raw(b"not valid json")
    ps.push("status_changed", {"status": "COMPLETED"})
    ps.stop()

    frames = _collect(_sse_stream("job-1", ps, keepalive_sec=0.01))
    out = b"".join(frames)
    assert b"status_changed" in out
    # Garbage payload is dropped, not forwarded as a broken event.
    assert b"not valid json" not in out


def test_sse_stream_closes_on_completed_event() -> None:
    """``completed`` is terminal — the generator unsubscribes and returns."""
    ps = FakePubSub()
    ps.push("completed", {"package_url": "/media/packages/x.zip"})
    # Queue never ends; if the stream kept going it would block here.

    frames = _collect(_sse_stream("job-1", ps, keepalive_sec=0.01), max_frames=5)
    assert any(b"event: completed" in f for f in frames)
    assert ps.unsubscribed


# ---------------------------------------------------------------------------
# SSE view — headers + plumbing
# ---------------------------------------------------------------------------


def test_sse_view_requires_existing_job(client: APIClient) -> None:
    res = client.get(f"/api/jobs/{uuid.uuid4()}/events")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_sse_view_invalid_uuid_404(client: APIClient) -> None:
    res = client.get("/api/jobs/not-a-uuid/events")
    assert res.status_code == 404


def test_sse_view_sets_required_headers(client: APIClient) -> None:
    job = _make_job()

    # Patch redis so the view doesn't try to open a real connection —
    # we only care about the response headers here.
    fake_client = MagicMock()
    fake_pubsub = MagicMock()
    fake_pubsub.get_message.return_value = None  # yields keepalives
    fake_client.pubsub.return_value = fake_pubsub

    with patch("api.views.jobs.redis.Redis.from_url", return_value=fake_client):
        res = client.get(f"/api/jobs/{job.id}/events")

    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/event-stream")
    assert res["Cache-Control"] == "no-cache"
    assert res["X-Accel-Buffering"] == "no"
    assert res["Connection"] == "keep-alive"
    # Subscription to the correct channel happened.
    fake_pubsub.subscribe.assert_called_once_with(f"job:{job.id}")
