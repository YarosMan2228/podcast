"""API-key preflight: catch placeholder/missing keys before they burn jobs.

Two layers of validation:

* :func:`check_api_keys(probe_network=False)` — fast structural check
  (non-empty + not a placeholder). Used inside the upload view to fail
  the request *before* a Job is persisted; runs every call so a key
  swap takes effect immediately, no cache.

* :func:`check_api_keys(probe_network=True)` — additionally pings each
  vendor with the cheapest call available (OpenAI list-models,
  Anthropic 1-token /v1/messages). Used by the management command and
  by `JobsConfig.ready()` for a startup banner. Network probes are
  cached for ``PROBE_TTL_SEC`` so a hot upload path doesn't pay the
  round-trip.

Each issue is returned as ``{"key": "OPENAI_API_KEY", "reason": "..."}``;
empty list means everything's fine.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


PROBE_TTL_SEC: float = 60.0

# Placeholder fragments — case-insensitive substring match. Kept narrow on
# purpose: real keys never contain words like "place" or angle brackets.
_PLACEHOLDER_PATTERN = re.compile(
    r"(place|your[-_]?key|xxx|<|>|replace|todo|example|change[-_]?me|sk-\.\.\.)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ProbeCacheEntry:
    issues: tuple[dict[str, str], ...]
    expires_at: float


_probe_cache: _ProbeCacheEntry | None = None
_probe_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Structural check (fast, no network)
# ---------------------------------------------------------------------------


def _structural_issues() -> list[dict[str, str]]:
    """Return issues for missing or obviously-placeholder keys."""
    issues: list[dict[str, str]] = []
    for key_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        value = (getattr(settings, key_name, "") or "").strip()
        if not value:
            issues.append(
                {
                    "key": key_name,
                    "reason": f"{key_name} is empty. Set a real key in .env.",
                }
            )
            continue
        if _PLACEHOLDER_PATTERN.search(value):
            issues.append(
                {
                    "key": key_name,
                    "reason": (
                        f"{key_name} appears to be a placeholder "
                        f"({value[:8]}…). Set a real key in .env."
                    ),
                }
            )
    return issues


# ---------------------------------------------------------------------------
# Network probes — best-effort, swallow errors as "not configured"
# ---------------------------------------------------------------------------


def _probe_openai() -> str | None:
    """Return reason on failure, ``None`` on success."""
    try:
        from openai import (  # type: ignore
            AuthenticationError,
            OpenAI,
            PermissionDeniedError,
        )
    except ImportError:  # pragma: no cover
        return None  # SDK not installed → can't probe; structural pass is enough.

    try:
        # ``models.list`` is the cheapest authenticated call: no tokens billed,
        # no audio upload, returns immediately. 401 → bad key. Network errors
        # are *not* an issue we can blame on the key, so swallow them.
        client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=5.0)
        client.models.list()
        return None
    except (AuthenticationError, PermissionDeniedError) as exc:
        return f"OpenAI rejected the key: {exc}"
    except Exception as exc:
        # Network/timeout/etc — don't fail the whole preflight on this; just
        # log it. The structural check already gated against placeholders.
        logger.warning(
            "preflight_openai_probe_inconclusive", extra={"error": str(exc)}
        )
        return None


def _probe_anthropic() -> str | None:
    try:
        from anthropic import (  # type: ignore
            Anthropic,
            AuthenticationError,
            PermissionDeniedError,
        )
    except ImportError:  # pragma: no cover
        return None

    try:
        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=5.0)
        # Minimum-billable Claude call: 1 output token. ~$0.000004 worst-case.
        client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return None
    except (AuthenticationError, PermissionDeniedError) as exc:
        return f"Anthropic rejected the key: {exc}"
    except Exception as exc:
        logger.warning(
            "preflight_anthropic_probe_inconclusive", extra={"error": str(exc)}
        )
        return None


def _network_issues() -> list[dict[str, str]]:
    """Run each vendor probe; return issues for any that explicitly rejected."""
    issues: list[dict[str, str]] = []
    if reason := _probe_openai():
        issues.append({"key": "OPENAI_API_KEY", "reason": reason})
    if reason := _probe_anthropic():
        issues.append({"key": "ANTHROPIC_API_KEY", "reason": reason})
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_api_keys(*, probe_network: bool = False) -> list[dict[str, str]]:
    """Return a list of preflight issues; empty list = ready to serve.

    ``probe_network=False`` is the upload-path default — purely structural,
    runs in microseconds. ``probe_network=True`` adds vendor pings cached
    for ``PROBE_TTL_SEC`` so we don't burn a round-trip on every request.
    """
    issues = _structural_issues()
    if not probe_network:
        return issues
    # Network probes only make sense if structural check passed; otherwise
    # we're guaranteed a 401 anyway and want the clearer "placeholder" error.
    if issues:
        return issues

    global _probe_cache
    now = time.monotonic()
    with _probe_lock:
        if _probe_cache and _probe_cache.expires_at > now:
            return list(_probe_cache.issues)
        net = _network_issues()
        _probe_cache = _ProbeCacheEntry(
            issues=tuple(net), expires_at=now + PROBE_TTL_SEC
        )
        return net


def reset_cache() -> None:
    """Drop the network-probe cache (tests use this between cases)."""
    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def issues_to_message(issues: list[dict[str, str]]) -> str:
    """Render issues as a one-line human message for the API envelope."""
    if not issues:
        return ""
    return "; ".join(f"{i['key']}: {i['reason']}" for i in issues)
