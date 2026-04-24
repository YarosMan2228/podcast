"""services.whisper_client — retry, backoff, and response normalization.

The ``openai`` package is lazily imported inside the client, so tests
construct tiny stand-in exception classes and fake response objects rather
than pulling the real SDK.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import whisper_client as wc
from services.whisper_client import WhisperError, transcribe


# ---------- exception stand-ins ----------


class _FakeRateLimit(Exception):
    pass


class _FakeAPIError(Exception):
    pass


class _FakeAPITimeout(Exception):
    pass


class _FakeConnection(Exception):
    pass


class _FakeBadRequest(Exception):
    pass


@pytest.fixture(autouse=True)
def _stub_openai_exception_hierarchy():
    """Inject our stand-in exception classes where the client imports them."""
    with patch.object(
        wc,
        "_retryable_exceptions",
        return_value=(_FakeRateLimit, _FakeAPIError, _FakeAPITimeout, _FakeConnection),
    ), patch.object(
        wc, "_permanent_exceptions", return_value=(_FakeBadRequest,)
    ), patch.object(wc, "_sleep"):
        yield


@pytest.fixture
def tmp_audio(tmp_path):
    p = tmp_path / "ep.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 1024)
    return str(p)


def _fake_response(
    *, language: str = "en", text: str = "Hello", duration: float = 3.4,
    segments=None, words=None,
):
    resp = MagicMock()
    resp.language = language
    resp.text = text
    resp.duration = duration
    resp.segments = segments or []
    resp.words = words or []
    return resp


def _fake_client(response=None, *, side_effects=None):
    """Build a minimal double for OpenAI() with .audio.transcriptions.create."""
    client = MagicMock()
    if side_effects is not None:
        client.audio.transcriptions.create.side_effect = side_effects
    else:
        client.audio.transcriptions.create.return_value = response or _fake_response()
    return client


# ---------- happy path + response normalization ----------


def test_transcribe_returns_normalized_result(tmp_audio):
    seg = MagicMock()
    seg.id = 0
    seg.start = 0.0
    seg.end = 1.2
    seg.text = "Hello there"
    word = MagicMock()
    word.word = "Hello"
    word.start = 0.0
    word.end = 0.5
    seg.words = [word]

    resp = _fake_response(segments=[seg], words=[word], text="Hello there", duration=1.2)
    result = transcribe(tmp_audio, client=_fake_client(resp))

    assert result.language == "en"
    assert result.full_text == "Hello there"
    assert result.duration_sec == pytest.approx(1.2)
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Hello there"
    assert result.segments[0]["words"][0]["word"] == "Hello"


def test_transcribe_passes_correct_api_params(tmp_audio):
    client = _fake_client()
    transcribe(tmp_audio, client=client, model="whisper-1")
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "whisper-1"
    assert kwargs["response_format"] == "verbose_json"
    assert set(kwargs["timestamp_granularities"]) == {"word", "segment"}


# ---------- retry behaviour (SPEC §3.4) ----------


def test_transcribe_retries_transient_then_succeeds(tmp_audio):
    resp = _fake_response()
    client = _fake_client(side_effects=[_FakeRateLimit("429"), _FakeAPIError("500"), resp])

    result = transcribe(tmp_audio, client=client)
    assert result.language == "en"
    assert client.audio.transcriptions.create.call_count == 3


def test_transcribe_gives_up_after_max_attempts(tmp_audio):
    client = _fake_client(side_effects=[_FakeRateLimit("429")] * wc.MAX_ATTEMPTS)

    with pytest.raises(WhisperError) as exc:
        transcribe(tmp_audio, client=client)
    assert exc.value.transient is True
    assert client.audio.transcriptions.create.call_count == wc.MAX_ATTEMPTS


def test_transcribe_permanent_error_is_not_retried(tmp_audio):
    client = _fake_client(side_effects=[_FakeBadRequest("invalid file")])
    with pytest.raises(WhisperError) as exc:
        transcribe(tmp_audio, client=client)
    assert exc.value.transient is False
    assert client.audio.transcriptions.create.call_count == 1


def test_transcribe_uses_exponential_backoff(tmp_audio):
    client = _fake_client(side_effects=[_FakeAPITimeout("x"), _FakeAPITimeout("x"), _fake_response()])
    with patch.object(wc, "_sleep") as slept:
        transcribe(tmp_audio, client=client)
    # Two retries → backoff 1s, 2s (base 1s * 2^(attempt-1)).
    slept.assert_any_call(wc.BACKOFF_BASE_SEC)
    slept.assert_any_call(wc.BACKOFF_BASE_SEC * 2)
