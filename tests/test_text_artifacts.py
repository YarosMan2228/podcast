"""Tests for text artifact workers and prompt builders — SPEC §6.

Coverage:
  - All 5 Celery tasks (happy path, validation, error → FAILED)
  - LinkedIn word-limit retry + programmatic truncation
  - Twitter JSON parse-retry + tweet-split at 280 chars
  - Code-fence stripping
  - Prompt builder functions (pure unit tests, no DB)
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator
from unittest.mock import call, patch

import pytest

from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
    Transcript,
)
from pipeline.prompts.text_artifacts import (
    DEFAULT_TONES,
    TONES,
    build_linkedin_prompt,
    build_newsletter_prompt,
    build_show_notes_prompt,
    build_twitter_prompt,
    build_youtube_description_prompt,
)
from workers.text_artifact_worker import (
    _count_words,
    _split_tweet_at_limit,
    _strip_code_fences,
    _truncate_to_word_limit,
    generate_linkedin_post,
    generate_newsletter,
    generate_show_notes,
    generate_twitter_thread,
    generate_youtube_description,
)

pytestmark = pytest.mark.django_db


@contextmanager
def zero_retries(task: Any) -> Generator[None, None, None]:
    """Patch a Celery task's max_retries to 0 so the first failure is final.

    In Celery's eager test mode self.request.retries stays at 0, meaning
    the ``is_final`` guard only fires when max_retries == 0.
    """
    original = task.max_retries
    task.max_retries = 0
    try:
        yield
    finally:
        task.max_retries = original

# ─────────────────────────── fixtures ───────────────────────────


SAMPLE_TRANSCRIPT = (
    "Welcome to the show. Today we are talking about artificial intelligence "
    "and how it is changing the world of business. Our guest has built three "
    "successful AI startups and has strong opinions about where the industry "
    "is heading. Let us dive right in."
)

SAMPLE_ANALYSIS = {
    "episode_title": "The Hidden Cost of AI Hype",
    "hook": "Most AI startups are building on sand — here is why.",
    "guest_json": {"name": "Sarah Chen", "bio": "CTO at Anthropic Labs"},
    "themes_json": ["AI infrastructure", "founder economics", "technical debt"],
    "chapters_json": [
        {"start_ms": 0, "end_ms": 180000, "title": "Introduction"},
        {"start_ms": 180000, "end_ms": 720000, "title": "Sarah background"},
    ],
    "clip_candidates_json": [],
    "quotes_json": [
        {"text": "You cannot outrun technical debt with valuation", "speaker": "Sarah Chen", "ts_ms": 512300}
    ],
}


def _make_usage(**overrides: object) -> dict:
    base = {"claude_model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50}
    return {**base, **overrides}


@pytest.fixture()
def job() -> Job:
    return Job.objects.create(source_type=SourceType.FILE, status=JobStatus.GENERATING)


@pytest.fixture()
def transcript(job: Job) -> Transcript:
    return Transcript.objects.create(
        job=job,
        language="en",
        full_text=SAMPLE_TRANSCRIPT,
        duration_sec=3600.0,
    )


@pytest.fixture()
def analysis(job: Job) -> Analysis:
    return Analysis.objects.create(
        job=job,
        episode_title=SAMPLE_ANALYSIS["episode_title"],
        hook=SAMPLE_ANALYSIS["hook"],
        guest_json=SAMPLE_ANALYSIS["guest_json"],
        themes_json=SAMPLE_ANALYSIS["themes_json"],
        chapters_json=SAMPLE_ANALYSIS["chapters_json"],
        quotes_json=SAMPLE_ANALYSIS["quotes_json"],
        claude_model="claude-sonnet-4-6",
        input_tokens=5000,
        output_tokens=500,
    )


@pytest.fixture()
def full_job(job: Job, transcript: Transcript, analysis: Analysis) -> Job:
    """Job with transcript and analysis attached."""
    return job


def _make_artifact(job: Job, artifact_type: str, index: int = 0) -> Artifact:
    return Artifact.objects.create(job=job, type=artifact_type, index=index)


# ─────────────────── pure-function unit tests ───────────────────


class TestHelpers:
    def test_count_words(self) -> None:
        assert _count_words("one two three") == 3
        assert _count_words("  spaced  out  ") == 2
        assert _count_words("") == 0

    def test_strip_code_fences_removes_generic_fence(self) -> None:
        assert _strip_code_fences("```\nhello\n```") == "hello"

    def test_strip_code_fences_removes_language_fence(self) -> None:
        assert _strip_code_fences("```json\n{}\n```") == "{}"

    def test_strip_code_fences_noop_on_plain_text(self) -> None:
        assert _strip_code_fences("plain text") == "plain text"

    def test_truncate_to_word_limit_short_input(self) -> None:
        assert _truncate_to_word_limit("hello world", 100) == "hello world"

    def test_truncate_to_word_limit_at_sentence_boundary(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        # 8 words total; limit to 5 → truncate after "Second sentence."
        result = _truncate_to_word_limit(text, 5)
        assert result.endswith(".")
        assert _count_words(result) <= 5

    def test_truncate_to_word_limit_no_punctuation_fallback(self) -> None:
        text = "word " * 20
        result = _truncate_to_word_limit(text.strip(), 10)
        assert _count_words(result) <= 10

    def test_split_tweet_within_limit(self) -> None:
        assert _split_tweet_at_limit("short tweet", 270) == ["short tweet"]

    def test_split_tweet_exceeds_limit(self) -> None:
        long_tweet = "word " * 60  # 300 chars
        parts = _split_tweet_at_limit(long_tweet.strip(), 270)
        assert len(parts) == 2
        assert all(len(p) <= 270 for p in parts)

    def test_split_tweet_no_space_fallback(self) -> None:
        # A single 300-char word with no spaces
        long_tweet = "x" * 300
        parts = _split_tweet_at_limit(long_tweet, 270)
        assert len(parts) == 2
        assert parts[0] == "x" * 270


# ──────────────────────── prompt builder tests ──────────────────────


class TestPromptBuilders:
    def test_linkedin_prompt_contains_key_fields(self) -> None:
        prompt = build_linkedin_prompt(SAMPLE_ANALYSIS, "analytical")
        assert "Hidden Cost of AI Hype" in prompt
        assert "300–500 words" in prompt
        assert "analytical" in prompt

    def test_twitter_prompt_specifies_json_format(self) -> None:
        prompt = build_twitter_prompt(SAMPLE_ANALYSIS, "casual")
        assert '"tweets"' in prompt
        assert "270 characters" in prompt

    def test_show_notes_prompt_includes_guest_hint_when_present(self) -> None:
        prompt = build_show_notes_prompt(SAMPLE_ANALYSIS, "analytical")
        assert "About the guest" in prompt
        assert "Sarah Chen" in prompt

    def test_show_notes_prompt_skips_guest_when_absent(self) -> None:
        analysis_no_guest = {**SAMPLE_ANALYSIS, "guest_json": None}
        prompt = build_show_notes_prompt(analysis_no_guest, "analytical")
        assert "skip" in prompt.lower() or "no guest" in prompt.lower()

    def test_newsletter_prompt_mentions_substack(self) -> None:
        prompt = build_newsletter_prompt(SAMPLE_ANALYSIS, "casual")
        assert "Substack" in prompt
        assert "400 words" in prompt

    def test_youtube_prompt_mentions_seo(self) -> None:
        prompt = build_youtube_description_prompt(SAMPLE_ANALYSIS, "professional")
        assert "150 characters" in prompt
        assert "PODCAST_LINKS" in prompt

    def test_default_tones_cover_all_artifact_types(self) -> None:
        types = {
            "LINKEDIN_POST", "TWITTER_THREAD", "SHOW_NOTES",
            "NEWSLETTER", "YOUTUBE_DESCRIPTION",
        }
        assert set(DEFAULT_TONES.keys()) == types

    def test_all_tones_defined(self) -> None:
        assert TONES == {"analytical", "casual", "punchy", "professional"}


# ─────────────────── LinkedIn task tests ────────────────────────


PATCH_CALL = "workers.text_artifact_worker.call_text_artifact"
PATCH_PUBLISH = "workers.text_artifact_worker.publish"


class TestGenerateLinkedInPost:
    def test_happy_path_marks_ready(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.LINKEDIN_POST)
        post_text = " ".join(["word"] * 400)  # 400 words — within limit
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(post_text, usage)), patch(PATCH_PUBLISH):
            generate_linkedin_post.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.text_content == post_text
        assert artifact.metadata_json["word_count"] == 400
        assert artifact.metadata_json["tone"] == "analytical"
        assert artifact.metadata_json["claude_model"] == "claude-sonnet-4-6"

    def test_retries_when_over_500_words(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.LINKEDIN_POST)
        over_limit = " ".join(["word"] * 600)
        within_limit = " ".join(["word"] * 450)
        usage = _make_usage()

        with (
            patch(PATCH_CALL, side_effect=[(over_limit, usage), (within_limit, usage)]) as mock_call,
            patch(PATCH_PUBLISH),
        ):
            generate_linkedin_post.apply_async(args=[str(artifact.id)])

        assert mock_call.call_count == 2
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.metadata_json["word_count"] == 450

    def test_truncates_programmatically_after_two_overflows(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.LINKEDIN_POST)
        over_limit = "word. " * 300  # many short sentences, well over 500 words
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(over_limit, usage)), patch(PATCH_PUBLISH):
            generate_linkedin_post.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.metadata_json["word_count"] <= 500

    def test_strips_code_fences(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.LINKEDIN_POST)
        fenced = "```\n" + " ".join(["word"] * 100) + "\n```"
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(fenced, usage)), patch(PATCH_PUBLISH):
            generate_linkedin_post.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert "```" not in artifact.text_content

    def test_respects_tone_argument(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.LINKEDIN_POST)
        text = " ".join(["word"] * 300)
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(text, usage)) as mock_call, patch(PATCH_PUBLISH):
            generate_linkedin_post.apply_async(args=[str(artifact.id), "punchy"])

        artifact.refresh_from_db()
        assert artifact.metadata_json["tone"] == "punchy"

    def test_marks_failed_on_final_retry(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.LINKEDIN_POST)

        with zero_retries(generate_linkedin_post):
            with patch(PATCH_CALL, side_effect=RuntimeError("API down")), patch(PATCH_PUBLISH):
                generate_linkedin_post.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "API down" in artifact.error

    def test_task_decorator_settings(self) -> None:
        assert generate_linkedin_post.max_retries == 3
        assert generate_linkedin_post.soft_time_limit == 300
        assert generate_linkedin_post.time_limit == 330
        assert generate_linkedin_post.acks_late is True


# ─────────────────── Twitter task tests ─────────────────────────


class TestGenerateTwitterThread:
    def test_happy_path_stores_json(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.TWITTER_THREAD)
        tweets = ["First tweet hooks you in.", "Second insight here.", "Third tweet CTA {EPISODE_URL}"]
        json_response = json.dumps({"tweets": tweets})
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(json_response, usage)), patch(PATCH_PUBLISH):
            generate_twitter_thread.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        stored = json.loads(artifact.text_content)
        assert stored["tweets"] == tweets
        assert artifact.metadata_json["tweet_count"] == 3

    def test_retries_on_invalid_json(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.TWITTER_THREAD)
        invalid = "Here are your tweets: great content!"
        valid_json = json.dumps({"tweets": ["Tweet one.", "Tweet two."]})
        usage = _make_usage()

        with (
            patch(PATCH_CALL, side_effect=[(invalid, usage), (valid_json, usage)]) as mock_call,
            patch(PATCH_PUBLISH),
        ):
            generate_twitter_thread.apply_async(args=[str(artifact.id)])

        assert mock_call.call_count == 2
        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY

    def test_splits_tweets_over_280_chars(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.TWITTER_THREAD)
        long_tweet = "word " * 60  # ~300 chars
        json_response = json.dumps({"tweets": [long_tweet.strip(), "Short tweet."]})
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(json_response, usage)), patch(PATCH_PUBLISH):
            generate_twitter_thread.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        stored = json.loads(artifact.text_content)
        # The long tweet should have been split into at least 2 parts
        assert len(stored["tweets"]) >= 3
        for tweet in stored["tweets"]:
            assert len(tweet) <= 270

    def test_strips_json_code_fences(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.TWITTER_THREAD)
        fenced = '```json\n{"tweets": ["Hello!", "World."]}\n```'
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(fenced, usage)), patch(PATCH_PUBLISH):
            generate_twitter_thread.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        stored = json.loads(artifact.text_content)
        assert stored["tweets"] == ["Hello!", "World."]

    def test_marks_failed_on_final_retry(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.TWITTER_THREAD)

        with zero_retries(generate_twitter_thread):
            with patch(PATCH_CALL, side_effect=RuntimeError("timeout")), patch(PATCH_PUBLISH):
                generate_twitter_thread.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED


# ─────────────────── Show notes task tests ──────────────────────


class TestGenerateShowNotes:
    def test_happy_path(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.SHOW_NOTES)
        notes = "# Episode Title\n\n## Topics covered\n- AI\n- Startups"
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(notes, usage)), patch(PATCH_PUBLISH):
            generate_show_notes.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.text_content == notes
        assert artifact.metadata_json["tone"] == "analytical"

    def test_strips_code_fences(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.SHOW_NOTES)
        fenced = "```markdown\n# Title\n## Topics\n```"
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(fenced, usage)), patch(PATCH_PUBLISH):
            generate_show_notes.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert "```" not in artifact.text_content

    def test_marks_failed_on_api_error(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.SHOW_NOTES)

        with zero_retries(generate_show_notes):
            with patch(PATCH_CALL, side_effect=ConnectionError("network")), patch(PATCH_PUBLISH):
                generate_show_notes.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED


# ─────────────────── Newsletter task tests ──────────────────────


class TestGenerateNewsletter:
    def test_happy_path(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.NEWSLETTER)
        newsletter = "**Subject line:** AI Hype\n\nHook paragraph here.\n\n## Takeaway 1: Speed\nDetails."
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(newsletter, usage)), patch(PATCH_PUBLISH):
            generate_newsletter.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.text_content == newsletter
        assert artifact.metadata_json["tone"] == "casual"

    def test_uses_custom_tone(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.NEWSLETTER)
        text = "Professional newsletter content here."
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(text, usage)), patch(PATCH_PUBLISH):
            generate_newsletter.apply_async(args=[str(artifact.id), "professional"])

        artifact.refresh_from_db()
        assert artifact.metadata_json["tone"] == "professional"

    def test_marks_failed_on_error(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.NEWSLETTER)

        with zero_retries(generate_newsletter):
            with patch(PATCH_CALL, side_effect=ValueError("bad response")), patch(PATCH_PUBLISH):
                generate_newsletter.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED


# ─────────────── YouTube Description task tests ─────────────────


class TestGenerateYouTubeDescription:
    def test_happy_path(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.YOUTUBE_DESCRIPTION)
        description = "The best AI podcast episode you will watch this year.\n\n00:00 - Intro\n\n#AI\n\n{PODCAST_LINKS}"
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(description, usage)), patch(PATCH_PUBLISH):
            generate_youtube_description.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.READY
        assert artifact.text_content == description
        assert artifact.metadata_json["tone"] == "professional"

    def test_marks_failed_on_error(self, full_job: Job) -> None:
        artifact = _make_artifact(full_job, ArtifactType.YOUTUBE_DESCRIPTION)

        with zero_retries(generate_youtube_description):
            with patch(PATCH_CALL, side_effect=RuntimeError("quota exceeded")), patch(PATCH_PUBLISH):
                generate_youtube_description.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.status == ArtifactStatus.FAILED
        assert "quota exceeded" in artifact.error


# ─────────────── metadata_json stored from artifact ─────────────


class TestMetadataFromArtifact:
    """Tone stored in metadata_json is reused when task is called without tone arg."""

    def test_inherits_tone_from_existing_metadata(self, full_job: Job) -> None:
        artifact = Artifact.objects.create(
            job=full_job,
            type=ArtifactType.LINKEDIN_POST,
            index=0,
            metadata_json={"tone": "punchy"},
        )
        text = " ".join(["word"] * 300)
        usage = _make_usage()

        with patch(PATCH_CALL, return_value=(text, usage)), patch(PATCH_PUBLISH):
            generate_linkedin_post.apply_async(args=[str(artifact.id)])

        artifact.refresh_from_db()
        assert artifact.metadata_json["tone"] == "punchy"
