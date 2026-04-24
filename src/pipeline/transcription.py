"""Transcription — Whisper orchestration for a single Job.

Responsibilities (SPEC §3.4):

1. Chunk the normalized WAV when it exceeds the Whisper 25MB per-file limit.
2. Call the whisper client for each chunk.
3. Stitch segments/words back together with cumulative time offsets.
4. Apply edge-case gates (empty / wrong language / likely noise — SPEC §3.5).
5. Persist a ``Transcript`` row with the SPEC §1.4 shape.

The Whisper *wire* call lives in ``services.whisper_client``; this module
is where the pipeline decides *how* to use it.
"""
from __future__ import annotations

import logging
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings

from jobs.models import Job, Transcript
from services.whisper_client import WhisperError, WhisperResult, transcribe as whisper_transcribe

logger = logging.getLogger(__name__)


# Whisper API per-file limit is 25 MB. We gate at 24 MB so a tiny header
# overhead doesn't push an edge-case file over.
WHISPER_FILE_LIMIT_BYTES = 24 * 1024 * 1024

# Default chunk length for files > the limit. 10 min at 16kHz mono 16-bit ≈
# 19 MB — well under the 25 MB ceiling (SPEC §3.4).
CHUNK_SEC = 600

# Noise-detection gates (SPEC §3.5 "TRANSCRIPTION_LIKELY_NOISE"). The 5-segment
# minimum avoids false positives on very short transcripts where repetition
# is natural.
NOISE_MIN_SEGMENTS = 5
NOISE_REPETITION_THRESHOLD = 0.20

SUPPORTED_LANGUAGE = "en"


class TranscriptionError(Exception):
    """Pipeline-level transcription failure with a stable SPEC §3.5 code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _needs_chunking(path: str) -> bool:
    return os.path.getsize(path) > WHISPER_FILE_LIMIT_BYTES


def _split_wav(path: str, chunk_sec: int = CHUNK_SEC) -> list[tuple[str, int]]:
    """Split *path* into ``chunk_sec``-long WAV files in a temp dir.

    Returns a list of ``(chunk_path, offset_ms)`` pairs. Cleanup is caller's
    responsibility — use ``_cleanup_chunks`` in a ``finally``.
    """
    from pydub import AudioSegment  # pydub pulls in ffmpeg at import time on some platforms

    audio = AudioSegment.from_wav(path)
    total_ms = len(audio)
    chunk_ms = chunk_sec * 1000

    tmp_dir = tempfile.mkdtemp(prefix="whisper_chunks_")
    chunks: list[tuple[str, int]] = []
    for offset_ms in range(0, total_ms, chunk_ms):
        piece = audio[offset_ms : offset_ms + chunk_ms]
        chunk_path = str(Path(tmp_dir) / f"chunk_{offset_ms:010d}.wav")
        piece.export(chunk_path, format="wav")
        chunks.append((chunk_path, offset_ms))
    return chunks


def _cleanup_chunks(chunks: list[tuple[str, int]]) -> None:
    for path, _ in chunks:
        try:
            os.remove(path)
        except OSError:
            pass
    if chunks:
        parent = Path(chunks[0][0]).parent
        try:
            parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------


def _stitch(results: list[tuple[WhisperResult, int]]) -> dict[str, Any]:
    """Merge per-chunk WhisperResults into SPEC §1.4 shape.

    *results* is ``[(chunk_result, offset_ms), ...]``. Offsets are added to
    each segment's/word's start and end, seconds are converted to ms, and
    segment ids are re-numbered monotonically across chunks.
    """
    merged_segments: list[dict[str, Any]] = []
    merged_text_parts: list[str] = []
    language = ""
    total_duration_sec = 0.0
    next_segment_id = 0

    for result, offset_ms in results:
        if not language and result.language:
            language = result.language
        total_duration_sec += result.duration_sec
        if result.full_text:
            merged_text_parts.append(result.full_text.strip())

        # Prefer per-segment words (present when timestamp_granularities
        # includes both "word" and "segment"); fall back to slicing the
        # top-level words list by segment time range.
        top_words = result.words

        for seg in result.segments:
            seg_start_ms = int(seg["start"] * 1000) + offset_ms
            seg_end_ms = int(seg["end"] * 1000) + offset_ms
            words_src = seg.get("words") or _words_in_range(
                top_words, seg["start"], seg["end"]
            )
            merged_segments.append(
                {
                    "id": next_segment_id,
                    "start_ms": seg_start_ms,
                    "end_ms": seg_end_ms,
                    "text": seg["text"].strip(),
                    "words": [
                        {
                            "w": w["word"].strip(),
                            "start_ms": int(w["start"] * 1000) + offset_ms,
                            "end_ms": int(w["end"] * 1000) + offset_ms,
                        }
                        for w in words_src
                    ],
                }
            )
            next_segment_id += 1

    return {
        "language": language,
        "full_text": " ".join(merged_text_parts).strip(),
        "duration_sec": total_duration_sec,
        "segments": merged_segments,
    }


def _words_in_range(words: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    """Return words whose midpoint falls inside [start, end] (seconds)."""
    out: list[dict[str, Any]] = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2.0
        if start <= mid <= end:
            out.append(w)
    return out


# ---------------------------------------------------------------------------
# Public pipeline entry points
# ---------------------------------------------------------------------------


def transcribe_file(path: str, *, job_id: str | None = None) -> dict[str, Any]:
    """Transcribe *path* (a normalized WAV) and return SPEC §1.4 dict.

    Chunks the file transparently when over the Whisper size limit and
    stitches the segments back together. Surfaces ``WhisperError`` from the
    underlying client unchanged — the caller decides how to fail the job.
    """
    if _needs_chunking(path):
        chunks = _split_wav(path)
        try:
            results = [
                (whisper_transcribe(chunk_path, job_id=job_id), offset_ms)
                for chunk_path, offset_ms in chunks
            ]
        finally:
            _cleanup_chunks(chunks)
        return _stitch(results)

    result = whisper_transcribe(path, job_id=job_id)
    return _stitch([(result, 0)])


def _is_likely_noise(segments: list[dict[str, Any]]) -> bool:
    """SPEC §3.5: ≥20% identical non-empty segments on a transcript with
    enough segments to distinguish noise from terse content."""
    texts = [s["text"].strip().lower() for s in segments if s["text"].strip()]
    if len(texts) < NOISE_MIN_SEGMENTS:
        return False
    most_common_count = Counter(texts).most_common(1)[0][1]
    return (most_common_count / len(texts)) >= NOISE_REPETITION_THRESHOLD


def transcribe_job(job_id: str) -> None:
    """Full transcription step for *job_id*.

    Loads the normalized WAV path from the Job row, calls Whisper (chunked
    if needed), enforces SPEC §3.5 edge cases, and persists a Transcript
    record via ``update_or_create`` (idempotent per celery-tasks.md §3).

    Raises ``TranscriptionError`` on pipeline failure — the Celery task
    layer translates that into the FAILED transition.
    """
    job = Job.objects.get(id=job_id)
    if not job.normalized_wav_path:
        raise TranscriptionError(
            "TRANSCRIPTION_NO_SOURCE",
            f"Job {job_id} has no normalized_wav_path; ingestion did not complete",
        )

    try:
        transcript = transcribe_file(job.normalized_wav_path, job_id=job_id)
    except WhisperError as exc:
        code = "TRANSCRIPTION_SERVICE_DOWN" if exc.transient else "TRANSCRIPTION_INVALID_INPUT"
        raise TranscriptionError(code, str(exc)) from exc

    full_text = transcript["full_text"].strip()
    if not full_text:
        raise TranscriptionError(
            "TRANSCRIPTION_EMPTY",
            "Whisper returned no speech — audio is silent or unintelligible",
        )

    if transcript["language"] != SUPPORTED_LANGUAGE:
        raise TranscriptionError(
            "TRANSCRIPTION_UNSUPPORTED_LANGUAGE",
            f"Detected language {transcript['language']!r}; MVP supports only English",
        )

    if _is_likely_noise(transcript["segments"]):
        raise TranscriptionError(
            "TRANSCRIPTION_LIKELY_NOISE",
            "Transcript has too many identical segments (Whisper likely hallucinated on noise)",
        )

    Transcript.objects.update_or_create(
        job=job,
        defaults={
            "language": transcript["language"],
            "full_text": full_text,
            "segments_json": transcript["segments"],
            "duration_sec": transcript["duration_sec"],
            "whisper_model": settings.WHISPER_MODEL,
        },
    )
    logger.info(
        "transcription_completed",
        extra={
            "job_id": job_id,
            "segments": len(transcript["segments"]),
            "duration_sec": transcript["duration_sec"],
            "language": transcript["language"],
        },
    )
