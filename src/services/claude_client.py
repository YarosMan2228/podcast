"""Anthropic Claude API client — SPEC §4.4, §6.6.

All Claude calls go through this module. Implements prompt caching:
the transcript block is marked ``cache_control=ephemeral`` so downstream
text-artifact workers share the cached tokens within the 5-minute TTL window,
saving up to 90 % of input-token costs for repeated calls on the same job.
"""
from __future__ import annotations

import logging
from typing import Any

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

# Beta header required to activate prompt-caching on the API side.
_PROMPT_CACHE_BETA = "prompt-caching-2024-07-31"


def _make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def call_text_artifact(
    transcript_text: str,
    user_message: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> tuple[str, dict[str, Any]]:
    """Call Claude with the transcript prompt-cached in the system message.

    The transcript block is sent with ``cache_control: {"type": "ephemeral"}``
    so it is reused across all five text-artifact tasks for the same job.

    Args:
        transcript_text: Full transcript (cached for 5 min across calls).
        user_message: Generation instruction for this specific artifact type.
        max_tokens: Maximum tokens for the completion.
        temperature: Sampling temperature.

    Returns:
        ``(text_content, usage_dict)`` where *usage_dict* contains
        ``claude_model``, ``input_tokens``, ``output_tokens``.
    """
    model: str = settings.CLAUDE_MODEL
    client = _make_client()

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=[
            {
                "type": "text",
                "text": (
                    "You are an expert podcast content repurposing specialist. "
                    "You create compelling, publication-ready content for various "
                    "platforms from podcast transcripts. Always follow the exact "
                    "format and length constraints specified in each request."
                ),
            },
            {
                "type": "text",
                "text": f"<transcript>\n{transcript_text}\n</transcript>",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_message}],
        extra_headers={"anthropic-beta": _PROMPT_CACHE_BETA},
    )

    text = response.content[0].text
    usage: dict[str, Any] = {
        "claude_model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    logger.info(
        "claude_call_completed",
        extra={
            "model": model,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
        },
    )
    return text, usage
