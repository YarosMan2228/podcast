"""services.claude_client — retry, prompt caching shape, usage logging."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import claude_client as cc
from services.claude_client import ClaudeError, ClaudeResponse, claude_client


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
def _stub_anthropic_exception_hierarchy():
    with patch.object(
        cc,
        "_retryable_exceptions",
        return_value=(_FakeRateLimit, _FakeAPIError, _FakeAPITimeout, _FakeConnection),
    ), patch.object(
        cc, "_permanent_exceptions", return_value=(_FakeBadRequest,)
    ), patch.object(cc, "_sleep"):
        yield


def _fake_response(
    *, text: str = "{}", input_tokens: int = 100, output_tokens: int = 50,
    cache_read: int = 0, cache_create: int = 0, stop_reason: str = "end_turn",
):
    resp = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_create
    resp.usage = usage
    resp.stop_reason = stop_reason
    return resp


def _fake_sdk(response=None, *, side_effects=None):
    sdk = MagicMock()
    if side_effects is not None:
        sdk.messages.create.side_effect = side_effects
    else:
        sdk.messages.create.return_value = response or _fake_response()
    return sdk


# ---------- happy path + response normalization ----------


def test_call_normalizes_text_and_usage():
    sdk = _fake_sdk(_fake_response(text="hello world", input_tokens=42, output_tokens=17,
                                    cache_read=8, cache_create=4))
    out = claude_client.call(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        temperature=0.3,
        prompt_name="analysis",
        client=sdk,
    )
    assert isinstance(out, ClaudeResponse)
    assert out.text == "hello world"
    assert out.input_tokens == 42
    assert out.output_tokens == 17
    assert out.cache_read_tokens == 8
    assert out.cache_creation_tokens == 4
    assert out.stop_reason == "end_turn"


def test_call_passes_system_and_messages_through():
    """System blocks must reach the SDK verbatim so cache_control is preserved."""
    sdk = _fake_sdk()
    system = [
        {"type": "text", "text": "You are a podcast analyst."},
        {"type": "text", "text": "<transcript>...</transcript>",
         "cache_control": {"type": "ephemeral"}},
    ]
    messages = [{"role": "user", "content": "task"}]
    claude_client.call(
        system=system, messages=messages, max_tokens=8000, temperature=0.3,
        prompt_name="analysis", client=sdk,
    )
    kwargs = sdk.messages.create.call_args.kwargs
    assert kwargs["system"] is system  # same list, cache_control intact
    assert kwargs["messages"] is messages
    assert kwargs["max_tokens"] == 8000
    assert kwargs["temperature"] == 0.3


# ---------- retry behaviour ----------


def test_call_retries_transient_then_succeeds():
    sdk = _fake_sdk(side_effects=[_FakeRateLimit("429"), _FakeAPIError("500"), _fake_response()])
    out = claude_client.call(
        system="s", messages=[{"role": "user", "content": "x"}],
        max_tokens=100, temperature=0.3, prompt_name="t", client=sdk,
    )
    assert out.text == "{}"
    assert sdk.messages.create.call_count == 3


def test_call_gives_up_after_max_attempts():
    sdk = _fake_sdk(side_effects=[_FakeRateLimit("429")] * cc.MAX_ATTEMPTS)
    with pytest.raises(ClaudeError) as exc:
        claude_client.call(
            system="s", messages=[{"role": "user", "content": "x"}],
            max_tokens=100, temperature=0.3, prompt_name="t", client=sdk,
        )
    assert exc.value.transient is True
    assert sdk.messages.create.call_count == cc.MAX_ATTEMPTS


def test_call_permanent_error_not_retried():
    sdk = _fake_sdk(side_effects=[_FakeBadRequest("invalid")])
    with pytest.raises(ClaudeError) as exc:
        claude_client.call(
            system="s", messages=[{"role": "user", "content": "x"}],
            max_tokens=100, temperature=0.3, prompt_name="t", client=sdk,
        )
    assert exc.value.transient is False
    assert sdk.messages.create.call_count == 1


def test_call_uses_exponential_backoff():
    sdk = _fake_sdk(side_effects=[_FakeAPITimeout("x"), _FakeAPITimeout("x"), _fake_response()])
    with patch.object(cc, "_sleep") as slept:
        claude_client.call(
            system="s", messages=[{"role": "user", "content": "x"}],
            max_tokens=100, temperature=0.3, prompt_name="t", client=sdk,
        )
    slept.assert_any_call(cc.BACKOFF_BASE_SEC)
    slept.assert_any_call(cc.BACKOFF_BASE_SEC * 2)
