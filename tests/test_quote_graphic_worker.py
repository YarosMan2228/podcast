"""workers.quote_graphic_worker — SPEC §7.

Tests focus on:
* select_eligible_quotes filtering by length
* artifact status transitions QUEUED → PROCESSING → READY/FAILED
* quote slot selection (index % len(eligible)), template cycling
* SSE event emission
* Celery retry/final-fail behaviour
* Celery decorator conformance
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
)
from workers.quote_graphic_worker import (
    TEMPLATE_CYCLE,
    generate_quote_graphic,
    select_eligible_quotes,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def zero_retries(task):
    """Set max_retries=0 so the is_final branch fires on first exception in eager mode."""
    original = task.max_retries
    task.max_retries = 0
    try:
        yield
    finally:
        task.max_retries = original


def _make_job_with_analysis(quotes: list[dict]) -> tuple[Job, Analysis]:
    job = Job.objects.create(
        source_type=SourceType.FILE,
        status=JobStatus.GENERATING,
    )
    analysis = Analysis.objects.create(
        job=job,
        episode_title="Test Episode",
        hook="Test hook",
        themes_json=[],
        chapters_json=[],
        clip_candidates_json=[],
        quotes_json=quotes,
        claude_model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
    )
    return job, analysis


def _make_artifact(job: Job, index: int = 0, **kwargs) -> Artifact:
    defaults = {
        "type": ArtifactType.QUOTE_GRAPHIC,
        "index": index,
        "status": ArtifactStatus.QUEUED,
    }
    defaults.update(kwargs)
    return Artifact.objects.create(job=job, **defaults)


SAMPLE_QUOTES = [
    {"text": "A" * 50, "speaker": "Alice"},
    {"text": "B" * 100, "speaker": "Bob"},
    {"text": "C" * 150, "speaker": "Carol"},
]


# ---------------------------------------------------------------------------
# select_eligible_quotes
# ---------------------------------------------------------------------------


class TestSelectEligibleQuotes:
    def test_keeps_quotes_in_range(self) -> None:
        quotes = [
            {"text": "x" * 20, "speaker": "S"},
            {"text": "x" * 100, "speaker": "S"},
            {"text": "x" * 180, "speaker": "S"},
        ]
        assert len(select_eligible_quotes(quotes)) == 3

    def test_rejects_too_short(self) -> None:
        quotes = [{"text": "x" * 19, "speaker": "S"}]
        assert select_eligible_quotes(quotes) == []

    def test_rejects_too_long(self) -> None:
        quotes = [{"text": "x" * 181, "speaker": "S"}]
        assert select_eligible_quotes(quotes) == []

    def test_boundary_values_included(self) -> None:
        quotes = [
            {"text": "x" * 20, "speaker": "S"},
            {"text": "x" * 180, "speaker": "S"},
        ]
        result = select_eligible_quotes(quotes)
        assert len(result) == 2

    def test_empty_input(self) -> None:
        assert select_eligible_quotes([]) == []

    def test_none_input(self) -> None:
        assert select_eligible_quotes(None) == []

    def test_missing_text_key_treated_as_empty(self) -> None:
        quotes = [{"speaker": "S"}]
        assert select_eligible_quotes(quotes) == []


# ---------------------------------------------------------------------------
# generate_quote_graphic — happy path
# ---------------------------------------------------------------------------


@override_settings(
    ARTIFACTS_ROOT="/tmp/artifacts",
    MEDIA_ROOT="/tmp",
)
def test_generate_quote_graphic_happy_path(tmp_path: Path) -> None:
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)
    artifact = _make_artifact(job, index=0)

    with (
        patch("workers.quote_graphic_worker._mark_processing") as mock_processing,
        patch("services.graphic_renderer.render_quote_to_png") as mock_render,
        patch("workers.quote_graphic_worker._mark_ready") as mock_ready,
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    mock_processing.assert_called_once_with(artifact)
    mock_render.assert_called_once()
    mock_ready.assert_called_once()


@override_settings(ARTIFACTS_ROOT="/tmp/artifacts", MEDIA_ROOT="/tmp")
def test_mark_processing_called_before_render(tmp_path: Path) -> None:
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)
    artifact = _make_artifact(job, index=0)

    call_order: list[str] = []

    with (
        patch(
            "workers.quote_graphic_worker._mark_processing",
            side_effect=lambda a: call_order.append("processing"),
        ),
        patch(
            "services.graphic_renderer.render_quote_to_png",
            side_effect=lambda *a, **kw: call_order.append("render"),
        ),
        patch("workers.quote_graphic_worker._mark_ready"),
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    assert call_order == ["processing", "render"]


# ---------------------------------------------------------------------------
# Quote selection — slot = index % len(eligible)
# ---------------------------------------------------------------------------


def test_quote_slot_uses_modulo(tmp_path: Path) -> None:
    """With 3 eligible quotes, index=4 should use slot 4%3=1 (second quote)."""
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)
    artifact = _make_artifact(job, index=4)

    render_calls: list[dict] = []

    def capture_render(quote, speaker, output_path, *, template_id):
        render_calls.append({"quote": quote, "speaker": speaker})

    with (
        patch("workers.quote_graphic_worker._mark_processing"),
        patch("services.graphic_renderer.render_quote_to_png", side_effect=capture_render),
        patch("workers.quote_graphic_worker._mark_ready"),
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    assert render_calls[0]["quote"] == SAMPLE_QUOTES[1]["text"]
    assert render_calls[0]["speaker"] == SAMPLE_QUOTES[1]["speaker"]


# ---------------------------------------------------------------------------
# Template cycling
# ---------------------------------------------------------------------------


def test_template_cycles_by_index(tmp_path: Path) -> None:
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)

    render_calls: list[str] = []

    def capture_template(quote, speaker, output_path, *, template_id):
        render_calls.append(template_id)

    for idx in range(4):
        artifact = _make_artifact(job, index=idx)
        with (
            patch("workers.quote_graphic_worker._mark_processing"),
            patch("services.graphic_renderer.render_quote_to_png", side_effect=capture_template),
            patch("workers.quote_graphic_worker._mark_ready"),
            override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
        ):
            generate_quote_graphic(str(artifact.id))

    assert render_calls == [TEMPLATE_CYCLE[i % 2] for i in range(4)]


# ---------------------------------------------------------------------------
# SSE events
# ---------------------------------------------------------------------------


def test_artifact_ready_sse_event_emitted(tmp_path: Path) -> None:
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)
    artifact = _make_artifact(job, index=0)

    published: list[dict] = []

    def fake_publish(job_id, event, data):
        published.append({"job_id": job_id, "event": event, "data": data})

    with (
        patch("workers.quote_graphic_worker._mark_processing"),
        patch("services.graphic_renderer.render_quote_to_png"),
        patch("workers.quote_graphic_worker.publish", side_effect=fake_publish),
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.READY
    assert any(p["event"] == "artifact_ready" for p in published)
    ready_event = next(p for p in published if p["event"] == "artifact_ready")
    assert ready_event["data"]["artifact_id"] == str(artifact.id)
    assert ready_event["data"]["type"] == ArtifactType.QUOTE_GRAPHIC


def test_artifact_failed_sse_event_emitted(tmp_path: Path) -> None:
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)
    artifact = _make_artifact(job, index=0)

    published: list[dict] = []

    def fake_publish(job_id, event, data):
        published.append({"job_id": job_id, "event": event, "data": data})

    with (
        patch("workers.quote_graphic_worker._mark_processing"),
        patch(
            "services.graphic_renderer.render_quote_to_png",
            side_effect=RuntimeError("Playwright crashed"),
        ),
        patch("workers.quote_graphic_worker.publish", side_effect=fake_publish),
        zero_retries(generate_quote_graphic),
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    assert any(p["event"] == "artifact_failed" for p in published)
    failed_event = next(p for p in published if p["event"] == "artifact_failed")
    assert "Playwright crashed" in failed_event["data"]["error"]


# ---------------------------------------------------------------------------
# No eligible quotes → fail immediately
# ---------------------------------------------------------------------------


def test_no_eligible_quotes_marks_failed(tmp_path: Path) -> None:
    short_quotes = [{"text": "short", "speaker": "S"}]
    job, _ = _make_job_with_analysis(short_quotes)
    artifact = _make_artifact(job, index=0)

    with (
        zero_retries(generate_quote_graphic),
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.FAILED
    assert "eligible" in artifact.error.lower()


# ---------------------------------------------------------------------------
# Retry / final-fail behaviour
# ---------------------------------------------------------------------------


def test_render_failure_marks_artifact_failed_on_final_retry(tmp_path: Path) -> None:
    job, _ = _make_job_with_analysis(SAMPLE_QUOTES)
    artifact = _make_artifact(job, index=0)

    with (
        patch("workers.quote_graphic_worker._mark_processing"),
        patch(
            "services.graphic_renderer.render_quote_to_png",
            side_effect=RuntimeError("timeout"),
        ),
        zero_retries(generate_quote_graphic),
        override_settings(ARTIFACTS_ROOT=str(tmp_path), MEDIA_ROOT=str(tmp_path)),
    ):
        generate_quote_graphic(str(artifact.id))

    artifact.refresh_from_db()
    assert artifact.status == ArtifactStatus.FAILED
    assert "timeout" in artifact.error


# ---------------------------------------------------------------------------
# Celery decorator conformance
# ---------------------------------------------------------------------------


def test_celery_decorator_settings() -> None:
    task = generate_quote_graphic
    assert task.max_retries == 3
    assert task.soft_time_limit == 300
    assert task.time_limit == 330
    assert task.acks_late is True


def test_task_is_registered() -> None:
    from core.celery import celery_app

    assert "workers.quote_graphic_worker.generate_quote_graphic" in celery_app.tasks
