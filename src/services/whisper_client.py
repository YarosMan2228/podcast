"""OpenAI Whisper client — the single call site for audio transcription.

Keeps retry/backoff, timeout, and usage logging out of the pipeline layer.
Per SPEC §3.4:

- Transient errors (``RateLimitError``, ``APIError``, ``APITimeoutError``,
  ``APIConnectionError``) → exponential backoff 1, 2, 4, 8s, max 4 attempts
- Permanent errors (``BadRequestError`` — corrupt file, unsupported format) →
  no retry, raised immediately as ``WhisperError``

The client is stateless except for the lazily-built ``OpenAI`` instance;
production imports ``whisper_client`` (the module-level singleton), tests
monkey-patch ``whisper_client._client`` or pass their own via ``client=``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


# Retry parameters per SPEC §3.4. Tunable via patching for tests.
MAX_ATTEMPTS = 4
BACKOFF_BASE_SEC = 1.0  # attempts sleep 1, 2, 4, 8 seconds


class WhisperError(Exception):
    """Raised when transcription ultimately fails.

    ``transient`` distinguishes "retries exhausted" from "permanent error"
    so the caller can tag the failed-job reason appropriately (SPEC §3.5
    distinguishes ``TRANSCRIPTION_SERVICE_DOWN`` from invalid-input failures).
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        self.transient = transient
        super().__init__(message)


@dataclass(frozen=True)
class WhisperResult:
    """Normalized Whisper response — not yet in SPEC §1.4 shape.

    Fields map 1:1 to the raw response; the stitching layer
    (``pipeline.transcription``) converts seconds→ms, joins chunks, and adds
    our ``segments`` schema.
    """

    language: str
    full_text: str
    duration_sec: float
    segments: list[dict[str, Any]]
    words: list[dict[str, Any]]


def _get_openai():
    """Import lazily so unit tests don't need the ``openai`` package."""
    from openai import OpenAI  # type: ignore

    return OpenAI(api_key=settings.OPENAI_API_KEY or None)


def _retryable_exceptions() -> tuple[type[BaseException], ...]:
    """SPEC §3.4 retryable set — imported lazily to avoid a hard dep at import time."""
    try:
        from openai import (  # type: ignore
            APIConnectionError,
            APIError,
            APITimeoutError,
            RateLimitError,
        )
    except Exception:  # pragma: no cover - openai package guaranteed at runtime
        return ()
    return (RateLimitError, APIError, APITimeoutError, APIConnectionError)


def _permanent_exceptions() -> tuple[type[BaseException], ...]:
    try:
        from openai import BadRequestError  # type: ignore
    except Exception:  # pragma: no cover
        return ()
    return (BadRequestError,)


def _sleep(seconds: float) -> None:
    """Indirection so tests can patch sleep to zero without touching time.sleep globally."""
    time.sleep(seconds)


def transcribe(
    path: str,
    *,
    model: str | None = None,
    job_id: str | None = None,
    client: Any | None = None,
) -> WhisperResult:
    """Transcribe the file at *path* with word-level timestamps.

    *model* defaults to ``settings.WHISPER_MODEL`` (``whisper-1``). *job_id*
    is used for logging correlation only. *client* is an injection seam for
    tests — in production we build an ``OpenAI`` instance per call (cheap,
    it's just a config struct).

    Raises ``WhisperError(transient=True)`` if all retries exhausted on
    transient errors; ``WhisperError(transient=False)`` for permanent errors.
    """
    model = model or settings.WHISPER_MODEL
    retryable = _retryable_exceptions()
    permanent = _permanent_exceptions()

    last_exc: BaseException | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            oai = client if client is not None else _get_openai()
            with open(path, "rb") as fh:
                # verbose_json gives us segments + duration + language;
                # timestamp_granularities=["word","segment"] surfaces
                # per-word timings for subtitle karaoke-highlighting.
                resp = oai.audio.transcriptions.create(
                    model=model,
                    file=fh,
                    response_format="verbose_json",
                    timestamp_granularities=["word", "segment"],
                )
        except permanent as exc:  # type: ignore[misc]
            logger.warning(
                "whisper_permanent_error",
                extra={"job_id": job_id, "path": path, "error": str(exc), "attempt": attempt},
            )
            raise WhisperError(str(exc), transient=False) from exc
        except retryable as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt == MAX_ATTEMPTS:
                logger.error(
                    "whisper_retries_exhausted",
                    extra={"job_id": job_id, "path": path, "error": str(exc), "attempts": attempt},
                )
                raise WhisperError(
                    f"Whisper retries exhausted after {attempt} attempts: {exc}",
                    transient=True,
                ) from exc
            backoff = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
            logger.warning(
                "whisper_retryable_error",
                extra={
                    "job_id": job_id,
                    "path": path,
                    "error": str(exc),
                    "attempt": attempt,
                    "backoff_sec": backoff,
                },
            )
            _sleep(backoff)
            continue

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "whisper_call",
            extra={
                "job_id": job_id,
                "path": path,
                "model": model,
                "duration_ms": duration_ms,
                "attempt": attempt,
            },
        )
        return _build_result(resp)

    # Unreachable — either we return inside the loop or re-raise.
    raise WhisperError(
        f"Whisper call exited loop without result (last={last_exc})",
        transient=True,
    )


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """openai 1.x returns pydantic objects; fall back to dict access for tests."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _build_result(resp: Any) -> WhisperResult:
    """Normalize a raw Whisper verbose_json response into a ``WhisperResult``."""
    segments_raw = _get(resp, "segments", []) or []
    words_raw = _get(resp, "words", []) or []
    return WhisperResult(
        language=_get(resp, "language", "") or "",
        full_text=_get(resp, "text", "") or "",
        duration_sec=float(_get(resp, "duration", 0.0) or 0.0),
        segments=[_seg_to_dict(s) for s in segments_raw],
        words=[_word_to_dict(w) for w in words_raw],
    )


def _seg_to_dict(seg: Any) -> dict[str, Any]:
    return {
        "id": _get(seg, "id", 0),
        "start": float(_get(seg, "start", 0.0) or 0.0),
        "end": float(_get(seg, "end", 0.0) or 0.0),
        "text": _get(seg, "text", "") or "",
        "words": [_word_to_dict(w) for w in (_get(seg, "words", []) or [])],
    }


def _word_to_dict(w: Any) -> dict[str, Any]:
    return {
        "word": _get(w, "word", "") or "",
        "start": float(_get(w, "start", 0.0) or 0.0),
        "end": float(_get(w, "end", 0.0) or 0.0),
    }
