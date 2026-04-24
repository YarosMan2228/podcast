"""pipeline.analysis — Claude call + validation loop + Analysis persistence."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from jobs.models import Analysis, Job, SourceType, Transcript
from pipeline import analysis as an
from pipeline.analysis import (
    AnalysisError,
    _dedupe_overlapping_clips,
    _strip_markdown_fences,
    analyze_job,
)
from pipeline.prompts.analysis import (
    EpisodeAnalysisSchema,
    build_messages,
    retry_user_message,
)
from services.claude_client import ClaudeError, ClaudeResponse


pytestmark = pytest.mark.django_db


# ---------- helpers ----------


def _valid_analysis_payload():
    return {
        "episode_title": "The Hidden Cost of AI Hype",
        "hook": "Most AI startups are building on sand.",
        "guest": {"name": "Sarah Chen", "bio": "CTO at Anthropic Labs."},
        "themes": ["AI infrastructure", "founder economics", "technical debt"],
        "chapters": [
            {"start_ms": 0, "end_ms": 180_000, "title": "Introduction"},
            {"start_ms": 180_000, "end_ms": 720_000, "title": "Sarah's background"},
        ],
        "clip_candidates": [
            {"start_ms": 100_000, "end_ms": 140_000, "virality_score": 9,
             "reason": "punchline", "hook_text": "The dirty secret"},
            {"start_ms": 300_000, "end_ms": 340_000, "virality_score": 7,
             "reason": "insight", "hook_text": "Why it matters"},
        ],
        "notable_quotes": [
            {"text": "You can't outrun technical debt.", "speaker": "Sarah", "ts_ms": 512_300},
        ],
    }


def _claude_response(payload) -> ClaudeResponse:
    return ClaudeResponse(
        text=json.dumps(payload) if not isinstance(payload, str) else payload,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        stop_reason="end_turn",
    )


def _make_job_with_transcript(language: str = "en") -> Job:
    job = Job.objects.create(source_type=SourceType.FILE)
    Transcript.objects.create(
        job=job,
        language=language,
        full_text="Welcome to the show. Today we talk about AI.",
        segments_json=[
            {"id": 0, "start_ms": 0, "end_ms": 3000, "text": "Welcome to the show.", "words": []},
            {"id": 1, "start_ms": 3000, "end_ms": 6000, "text": "Today we talk about AI.", "words": []},
        ],
        duration_sec=6.0,
    )
    return job


# ---------- _strip_markdown_fences ----------


def test_strip_markdown_fences_plain_json_passes_through():
    assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'


def test_strip_markdown_fences_removes_json_tagged_block():
    text = "```json\n{\"a\": 1}\n```"
    assert _strip_markdown_fences(text) == '{"a": 1}'


def test_strip_markdown_fences_removes_bare_block():
    text = "```\n{\"a\": 1}\n```"
    assert _strip_markdown_fences(text) == '{"a": 1}'


# ---------- _dedupe_overlapping_clips ----------


def test_dedupe_overlapping_keeps_higher_score():
    clips = [
        {"start_ms": 100, "end_ms": 200, "virality_score": 6},
        {"start_ms": 150, "end_ms": 250, "virality_score": 9},  # overlaps, higher
        {"start_ms": 300, "end_ms": 400, "virality_score": 7},  # no overlap
    ]
    kept = _dedupe_overlapping_clips(clips)
    scores = sorted(c["virality_score"] for c in kept)
    assert scores == [7, 9]


def test_dedupe_preserves_non_overlapping_clips():
    clips = [
        {"start_ms": 0, "end_ms": 100, "virality_score": 5},
        {"start_ms": 200, "end_ms": 300, "virality_score": 5},
    ]
    assert len(_dedupe_overlapping_clips(clips)) == 2


# ---------- build_messages / prompt caching ----------


def test_build_messages_puts_transcript_in_cached_system_block():
    system, messages = build_messages(
        full_text="Hello world.",
        segments=[{"id": 0, "start_ms": 0, "end_ms": 1000, "text": "Hello world.", "words": []}],
    )
    # First block — static instructions, no cache_control.
    assert "cache_control" not in system[0]
    # Second block — transcript, ephemeral cache so downstream text artifacts
    # can reuse it within 5 minutes.
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert "Hello world." in system[1]["text"]
    assert "[id=0 start=0ms end=1000ms]" in system[1]["text"]
    # User message — just the task, no transcript.
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "Return ONLY" in messages[0]["content"]


def test_retry_user_message_echoes_previous_and_error():
    msgs = retry_user_message("bad response", "missing field X")
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "bad response"
    assert msgs[1]["role"] == "user"
    assert "missing field X" in msgs[1]["content"]


# ---------- schema ----------


def test_schema_accepts_valid_payload():
    EpisodeAnalysisSchema.model_validate(_valid_analysis_payload())


def test_schema_rejects_title_over_60_chars():
    payload = _valid_analysis_payload()
    payload["episode_title"] = "x" * 61
    with pytest.raises(Exception):
        EpisodeAnalysisSchema.model_validate(payload)


def test_schema_rejects_clip_with_inverted_range():
    payload = _valid_analysis_payload()
    payload["clip_candidates"][0]["end_ms"] = payload["clip_candidates"][0]["start_ms"] - 1
    with pytest.raises(Exception):
        EpisodeAnalysisSchema.model_validate(payload)


def test_schema_rejects_virality_out_of_range():
    payload = _valid_analysis_payload()
    payload["clip_candidates"][0]["virality_score"] = 11
    with pytest.raises(Exception):
        EpisodeAnalysisSchema.model_validate(payload)


# ---------- analyze_job orchestration ----------


def test_analyze_job_persists_analysis_row():
    job = _make_job_with_transcript()
    resp = _claude_response(_valid_analysis_payload())
    with patch.object(an.claude_client, "call", return_value=resp) as called:
        analyze_job(str(job.id))

    a = Analysis.objects.get(job=job)
    assert a.episode_title == "The Hidden Cost of AI Hype"
    assert a.guest_json == {"name": "Sarah Chen", "bio": "CTO at Anthropic Labs."}
    assert len(a.clip_candidates_json) == 2
    assert a.input_tokens == 1000
    assert a.output_tokens == 500
    # Called once — valid JSON on first try, no retry.
    assert called.call_count == 1
    kwargs = called.call_args.kwargs
    assert kwargs["prompt_name"] == "analysis"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["temperature"] == 0.3


def test_analyze_job_retries_on_invalid_json():
    job = _make_job_with_transcript()
    with patch.object(
        an.claude_client,
        "call",
        side_effect=[
            _claude_response("not JSON at all"),
            _claude_response(_valid_analysis_payload()),
        ],
    ) as called:
        analyze_job(str(job.id))

    assert called.call_count == 2
    # Second call should include the assistant-echo + corrective user message.
    second_messages = called.call_args_list[1].kwargs["messages"]
    assert any(m["role"] == "assistant" and m["content"] == "not JSON at all" for m in second_messages)
    assert any("validation" in m["content"].lower() for m in second_messages if m["role"] == "user")


def test_analyze_job_retries_on_schema_validation_error():
    job = _make_job_with_transcript()
    bad = _valid_analysis_payload()
    bad["episode_title"] = "x" * 200  # too long → schema rejects
    with patch.object(
        an.claude_client,
        "call",
        side_effect=[
            _claude_response(bad),
            _claude_response(_valid_analysis_payload()),
        ],
    ) as called:
        analyze_job(str(job.id))

    assert called.call_count == 2


def test_analyze_job_final_attempt_drops_temperature_to_zero():
    job = _make_job_with_transcript()
    with patch.object(
        an.claude_client,
        "call",
        side_effect=[
            _claude_response("not JSON"),
            _claude_response("still not JSON"),
            _claude_response(_valid_analysis_payload()),
        ],
    ) as called:
        analyze_job(str(job.id))

    # Final attempt — the third call — must use temperature=0.
    temperatures = [c.kwargs["temperature"] for c in called.call_args_list]
    assert temperatures[0] == 0.3
    assert temperatures[1] == 0.3
    assert temperatures[2] == 0.0


def test_analyze_job_fails_after_max_invalid_attempts():
    job = _make_job_with_transcript()
    with patch.object(
        an.claude_client,
        "call",
        side_effect=[_claude_response("not JSON")] * 3,
    ):
        with pytest.raises(AnalysisError) as exc:
            analyze_job(str(job.id))
    assert exc.value.code == "ANALYSIS_INVALID_JSON"


def test_analyze_job_raises_when_no_transcript():
    job = Job.objects.create(source_type=SourceType.FILE)
    with pytest.raises(AnalysisError) as exc:
        analyze_job(str(job.id))
    assert exc.value.code == "ANALYSIS_NO_TRANSCRIPT"


def test_analyze_job_maps_transient_claude_error():
    job = _make_job_with_transcript()
    with patch.object(
        an.claude_client,
        "call",
        side_effect=ClaudeError("retries exhausted", transient=True),
    ):
        with pytest.raises(AnalysisError) as exc:
            analyze_job(str(job.id))
    assert exc.value.code == "ANALYSIS_SERVICE_DOWN"


def test_analyze_job_dedupes_overlapping_clips_in_saved_row():
    job = _make_job_with_transcript()
    payload = _valid_analysis_payload()
    payload["clip_candidates"] = [
        {"start_ms": 100_000, "end_ms": 200_000, "virality_score": 6,
         "reason": "a", "hook_text": "h1"},
        {"start_ms": 150_000, "end_ms": 250_000, "virality_score": 9,
         "reason": "b", "hook_text": "h2"},  # overlaps first, higher score — wins
    ]
    with patch.object(an.claude_client, "call", return_value=_claude_response(payload)):
        analyze_job(str(job.id))

    a = Analysis.objects.get(job=job)
    assert len(a.clip_candidates_json) == 1
    assert a.clip_candidates_json[0]["virality_score"] == 9


def test_analyze_job_is_idempotent():
    """Re-running must update the existing Analysis row, not duplicate."""
    job = _make_job_with_transcript()
    with patch.object(
        an.claude_client, "call",
        return_value=_claude_response(_valid_analysis_payload()),
    ):
        analyze_job(str(job.id))
        analyze_job(str(job.id))

    assert Analysis.objects.filter(job=job).count() == 1
