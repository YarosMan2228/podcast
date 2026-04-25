"""Preflight: structural API-key validation + upload gating."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from services import preflight


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """Each test starts with a clean network-probe cache."""
    preflight.reset_cache()
    yield
    preflight.reset_cache()


# ---------------------------------------------------------------------------
# Structural check
# ---------------------------------------------------------------------------


@override_settings(OPENAI_API_KEY="sk-real-and-long-enough-aaaa", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_real_looking_keys_pass_structural() -> None:
    assert preflight.check_api_keys() == []


@override_settings(OPENAI_API_KEY="", ANTHROPIC_API_KEY="sk-ant-aaaa")
def test_empty_openai_key_is_flagged() -> None:
    issues = preflight.check_api_keys()
    assert len(issues) == 1
    assert issues[0]["key"] == "OPENAI_API_KEY"
    assert "empty" in issues[0]["reason"].lower()


@override_settings(OPENAI_API_KEY="sk-aaaa", ANTHROPIC_API_KEY="")
def test_empty_anthropic_key_is_flagged() -> None:
    issues = preflight.check_api_keys()
    assert [i["key"] for i in issues] == ["ANTHROPIC_API_KEY"]


@pytest.mark.parametrize(
    "placeholder",
    [
        "sk-placeholder-replace-me",
        "your-key-here",
        "<paste-key-here>",
        "TODO",
        "sk-...",
        "xxx-xxx-xxx",
        "change-me",
    ],
)
def test_placeholder_patterns_are_caught(placeholder: str) -> None:
    """Each common 'fill-me-in' string is recognised as a placeholder."""
    with override_settings(
        OPENAI_API_KEY=placeholder, ANTHROPIC_API_KEY="sk-ant-real-aaaa"
    ):
        issues = preflight.check_api_keys()
        assert len(issues) == 1
        assert issues[0]["key"] == "OPENAI_API_KEY"
        assert "placeholder" in issues[0]["reason"].lower()


@override_settings(
    OPENAI_API_KEY="sk-placeholder", ANTHROPIC_API_KEY="sk-ant-placeholder"
)
def test_both_keys_flagged_independently() -> None:
    issues = preflight.check_api_keys()
    assert {i["key"] for i in issues} == {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}


@override_settings(OPENAI_API_KEY="   sk-real-aaaa   ", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_whitespace_only_value_is_not_a_pass() -> None:
    """A real key with stray whitespace should still pass — only pure
    placeholder text should fail."""
    assert preflight.check_api_keys() == []


@override_settings(OPENAI_API_KEY="    ", ANTHROPIC_API_KEY="sk-ant-real-aaaa")
def test_whitespace_only_value_is_treated_as_empty() -> None:
    issues = preflight.check_api_keys()
    assert len(issues) == 1
    assert "empty" in issues[0]["reason"].lower()


# ---------------------------------------------------------------------------
# Network probe — patched, no real HTTP
# ---------------------------------------------------------------------------


@override_settings(
    OPENAI_API_KEY="sk-real-aaaa", ANTHROPIC_API_KEY="sk-ant-real-aaaa"
)
def test_network_probe_caches_for_ttl() -> None:
    """The second call inside the TTL window must not re-probe."""
    with patch("services.preflight._probe_openai", return_value=None) as oai, patch(
        "services.preflight._probe_anthropic", return_value=None
    ) as ant:
        preflight.check_api_keys(probe_network=True)
        preflight.check_api_keys(probe_network=True)
        preflight.check_api_keys(probe_network=True)

    assert oai.call_count == 1
    assert ant.call_count == 1


@override_settings(
    OPENAI_API_KEY="sk-real-aaaa", ANTHROPIC_API_KEY="sk-ant-real-aaaa"
)
def test_network_probe_failure_surfaces_in_issues() -> None:
    with patch(
        "services.preflight._probe_openai",
        return_value="OpenAI rejected the key: 401",
    ), patch("services.preflight._probe_anthropic", return_value=None):
        issues = preflight.check_api_keys(probe_network=True)
    assert [i["key"] for i in issues] == ["OPENAI_API_KEY"]
    assert "rejected" in issues[0]["reason"]


@override_settings(
    OPENAI_API_KEY="sk-placeholder-aaaa", ANTHROPIC_API_KEY="sk-ant-real-aaaa"
)
def test_network_probe_skipped_when_structural_already_failed() -> None:
    """No point pinging the API when we already know the key is fake —
    saves a guaranteed 401 round-trip per upload."""
    with patch("services.preflight._probe_openai") as oai, patch(
        "services.preflight._probe_anthropic"
    ) as ant:
        issues = preflight.check_api_keys(probe_network=True)

    assert len(issues) == 1
    assert issues[0]["key"] == "OPENAI_API_KEY"
    oai.assert_not_called()
    ant.assert_not_called()


# ---------------------------------------------------------------------------
# Helper formatting
# ---------------------------------------------------------------------------


def test_issues_to_message_joins_with_semicolons() -> None:
    msg = preflight.issues_to_message(
        [
            {"key": "OPENAI_API_KEY", "reason": "is empty"},
            {"key": "ANTHROPIC_API_KEY", "reason": "is placeholder"},
        ]
    )
    assert "OPENAI_API_KEY: is empty" in msg
    assert "ANTHROPIC_API_KEY: is placeholder" in msg
    assert msg.count(";") == 1


def test_issues_to_message_empty_returns_empty_string() -> None:
    assert preflight.issues_to_message([]) == ""
