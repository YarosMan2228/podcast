"""Anthropic Claude client — shared call site for every Claude invocation.

Rules owner: see ``.claude/rules/claude-api-usage.md``. Per §1, nothing else
in the codebase should instantiate ``anthropic.Anthropic`` — everything
goes through ``claude_client.call(...)``.

Key responsibilities:

- Retries on transient errors (RateLimit / APIError / APIConnection / APITimeout)
  with exponential backoff 1/2/4/8s, max 4 attempts (§6).
- Usage logging with job_id, prompt_name, input/output/cache token counts,
  duration_ms (§7).
- ``max_tokens`` and ``temperature`` are required arguments — no sloppy
  defaults (§2).
- Accepts ``system`` as either a str or the block-list form; the caller
  decides whether to mark a block with ``cache_control`` for §4 prompt
  caching (the transcript, re-used across Analysis + text artifacts).

The raw response is wrapped in a ``ClaudeResponse`` dataclass so callers
don't have to juggle the SDK's pydantic types directly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 4
BACKOFF_BASE_SEC = 1.0


class ClaudeError(Exception):
    """Raised after retries are exhausted (transient=True) or on permanent errors."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        self.transient = transient
        super().__init__(message)


@dataclass(frozen=True)
class ClaudeResponse:
    """Normalized slice of an Anthropic messages response.

    We only surface the fields the pipeline cares about — the raw response
    is intentionally not exposed so callers don't take a hard dep on SDK
    types.
    """

    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    stop_reason: str


def _get_anthropic():
    """Lazy import so the SDK isn't required at test-collection time."""
    from anthropic import Anthropic  # type: ignore

    return Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)


def _retryable_exceptions() -> tuple[type[BaseException], ...]:
    try:
        from anthropic import (  # type: ignore
            APIConnectionError,
            APIError,
            APITimeoutError,
            RateLimitError,
        )
    except Exception:  # pragma: no cover
        return ()
    return (RateLimitError, APIError, APITimeoutError, APIConnectionError)


def _permanent_exceptions() -> tuple[type[BaseException], ...]:
    try:
        from anthropic import BadRequestError  # type: ignore
    except Exception:  # pragma: no cover
        return ()
    return (BadRequestError,)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class ClaudeClient:
    """Thin wrapper — see module docstring.

    Not a singleton by construction, but a module-level instance
    ``claude_client`` is exported at the bottom for convenience. Tests
    inject a fake via the ``client`` parameter on ``call``.
    """

    def call(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        prompt_name: str,
        job_id: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> ClaudeResponse:
        model = model or settings.CLAUDE_MODEL
        retryable = _retryable_exceptions()
        permanent = _permanent_exceptions()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                sdk = client if client is not None else _get_anthropic()
                resp = sdk.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=messages,
                )
            except permanent as exc:  # type: ignore[misc]
                logger.warning(
                    "claude_permanent_error",
                    extra={
                        "job_id": job_id,
                        "prompt_name": prompt_name,
                        "error": str(exc),
                        "attempt": attempt,
                    },
                )
                raise ClaudeError(str(exc), transient=False) from exc
            except retryable as exc:  # type: ignore[misc]
                if attempt == MAX_ATTEMPTS:
                    logger.error(
                        "claude_retries_exhausted",
                        extra={
                            "job_id": job_id,
                            "prompt_name": prompt_name,
                            "error": str(exc),
                            "attempts": attempt,
                        },
                    )
                    raise ClaudeError(
                        f"Claude retries exhausted after {attempt} attempts: {exc}",
                        transient=True,
                    ) from exc
                backoff = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "claude_retryable_error",
                    extra={
                        "job_id": job_id,
                        "prompt_name": prompt_name,
                        "error": str(exc),
                        "attempt": attempt,
                        "backoff_sec": backoff,
                    },
                )
                _sleep(backoff)
                continue

            duration_ms = int((time.monotonic() - started) * 1000)
            normalized = _build_response(resp)
            logger.info(
                "claude_call",
                extra={
                    "job_id": job_id,
                    "prompt_name": prompt_name,
                    "model": model,
                    "input_tokens": normalized.input_tokens,
                    "cache_read_tokens": normalized.cache_read_tokens,
                    "cache_creation_tokens": normalized.cache_creation_tokens,
                    "output_tokens": normalized.output_tokens,
                    "duration_ms": duration_ms,
                    "attempt": attempt,
                    "stop_reason": normalized.stop_reason,
                },
            )
            return normalized

        # Unreachable — loop either returns or raises.
        raise ClaudeError("Claude call exited loop without result", transient=True)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _build_response(resp: Any) -> ClaudeResponse:
    # content is a list of blocks; we want the concatenated .text of type=text
    blocks = _attr(resp, "content", []) or []
    text_parts: list[str] = []
    for b in blocks:
        if _attr(b, "type", "") == "text":
            text_parts.append(_attr(b, "text", "") or "")
    usage = _attr(resp, "usage", None)
    return ClaudeResponse(
        text="".join(text_parts),
        input_tokens=int(_attr(usage, "input_tokens", 0) or 0),
        output_tokens=int(_attr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(_attr(usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(_attr(usage, "cache_creation_input_tokens", 0) or 0),
        stop_reason=_attr(resp, "stop_reason", "") or "",
    )


# Module-level singleton — import as ``from services.claude_client import claude_client``.
claude_client = ClaudeClient()
