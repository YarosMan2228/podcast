"""pipeline.transcription — chunking, stitching, and transcribe_job orchestration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jobs.models import Job, SourceType, Transcript
from pipeline import transcription as tr
from pipeline.transcription import (
    CHUNK_SEC,
    NOISE_MIN_SEGMENTS,
    TranscriptionError,
    _is_likely_noise,
    _stitch,
    transcribe_file,
    transcribe_job,
)
from services.whisper_client import WhisperError, WhisperResult


pytestmark = pytest.mark.django_db


def _result(
    *, language: str = "en", text: str = "Hello there", duration: float = 2.0,
    segments=None, words=None,
) -> WhisperResult:
    return WhisperResult(
        language=language,
        full_text=text,
        duration_sec=duration,
        segments=segments if segments is not None else [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "Hello", "words": [
                {"word": "Hello", "start": 0.0, "end": 0.5},
            ]},
            {"id": 1, "start": 1.0, "end": 2.0, "text": "there", "words": [
                {"word": "there", "start": 1.0, "end": 1.5},
            ]},
        ],
        words=words or [],
    )


# ---------- stitching ----------


def test_stitch_converts_seconds_to_ms_and_renumbers_ids():
    merged = _stitch([(_result(), 0)])
    assert merged["language"] == "en"
    assert merged["duration_sec"] == pytest.approx(2.0)
    assert len(merged["segments"]) == 2
    assert merged["segments"][0]["id"] == 0
    assert merged["segments"][0]["start_ms"] == 0
    assert merged["segments"][0]["end_ms"] == 1000
    # Word-level schema is SPEC §1.4 ("w", "start_ms", "end_ms").
    word = merged["segments"][0]["words"][0]
    assert word == {"w": "Hello", "start_ms": 0, "end_ms": 500}


def test_stitch_applies_cumulative_offsets_across_chunks():
    merged = _stitch([(_result(), 0), (_result(), 600_000)])

    # Chunk-2 segments shifted by 10 min = 600_000 ms.
    assert merged["segments"][2]["start_ms"] == 600_000
    assert merged["segments"][2]["end_ms"] == 601_000
    assert merged["segments"][3]["start_ms"] == 601_000
    assert merged["segments"][3]["words"][0]["start_ms"] == 601_000
    # IDs renumbered monotonically across chunks.
    assert [s["id"] for s in merged["segments"]] == [0, 1, 2, 3]
    # Duration is the sum of chunk durations.
    assert merged["duration_sec"] == pytest.approx(4.0)


def test_stitch_falls_back_to_toplevel_words_when_segment_lacks_words():
    """If verbose_json returns words only at the top level, we slice per segment."""
    r = WhisperResult(
        language="en",
        full_text="Hi world",
        duration_sec=2.0,
        segments=[
            {"id": 0, "start": 0.0, "end": 1.0, "text": "Hi", "words": []},
            {"id": 1, "start": 1.0, "end": 2.0, "text": "world", "words": []},
        ],
        words=[
            {"word": "Hi", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 1.1, "end": 1.8},
        ],
    )
    merged = _stitch([(r, 0)])
    assert [w["w"] for w in merged["segments"][0]["words"]] == ["Hi"]
    assert [w["w"] for w in merged["segments"][1]["words"]] == ["world"]


# ---------- chunking decision ----------


def test_transcribe_file_single_call_when_under_limit(tmp_path: Path):
    small = tmp_path / "small.wav"
    small.write_bytes(b"\x00" * 1024)

    with patch("pipeline.transcription.whisper_transcribe", return_value=_result()) as w:
        out = transcribe_file(str(small))

    assert w.call_count == 1
    assert out["language"] == "en"
    assert len(out["segments"]) == 2


def test_transcribe_file_chunks_when_over_limit(tmp_path: Path):
    big = tmp_path / "big.wav"
    big.write_bytes(b"\x00" * 1024)  # real content doesn't matter — we stub chunker

    fake_chunks = [
        (str(tmp_path / "c0.wav"), 0),
        (str(tmp_path / "c1.wav"), CHUNK_SEC * 1000),
    ]
    # Populate so _cleanup_chunks' os.remove won't raise noisily.
    for p, _ in fake_chunks:
        Path(p).write_bytes(b"\x00")

    with patch(
        "pipeline.transcription._needs_chunking", return_value=True
    ), patch(
        "pipeline.transcription._split_wav", return_value=fake_chunks
    ), patch(
        "pipeline.transcription.whisper_transcribe", side_effect=[_result(), _result()]
    ) as w:
        out = transcribe_file(str(big))

    assert w.call_count == 2
    # Second chunk's segments shifted by CHUNK_SEC * 1000 ms.
    assert out["segments"][2]["start_ms"] == CHUNK_SEC * 1000


# ---------- noise detection ----------


def test_is_likely_noise_true_on_repetitive_hallucination():
    # 5 segments, 4 are "thank you for watching" → 80% repetition.
    segments = [
        {"text": "thank you for watching"},
        {"text": "thank you for watching"},
        {"text": "thank you for watching"},
        {"text": "thank you for watching"},
        {"text": "real content here"},
    ]
    assert _is_likely_noise(segments) is True


def test_is_likely_noise_false_when_varied():
    segments = [{"text": f"unique content {i}"} for i in range(NOISE_MIN_SEGMENTS + 3)]
    assert _is_likely_noise(segments) is False


def test_is_likely_noise_false_on_short_transcript():
    """A terse 3-segment transcript can genuinely have repeated phrases."""
    segments = [{"text": "yes"}, {"text": "yes"}, {"text": "yes"}]
    assert _is_likely_noise(segments) is False


# ---------- transcribe_job orchestration ----------


def test_transcribe_job_persists_transcript_and_sets_model(tmp_path: Path):
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE,
        normalized_wav_path=str(wav),
        duration_sec=2.0,
    )

    with patch("pipeline.transcription.whisper_transcribe", return_value=_result()):
        transcribe_job(str(job.id))

    t = Transcript.objects.get(job=job)
    assert t.language == "en"
    assert t.full_text == "Hello there"
    assert len(t.segments_json) == 2
    assert t.whisper_model == "whisper-1"


def test_transcribe_job_is_idempotent(tmp_path: Path):
    """Re-running the task must update the existing row, not insert a duplicate."""
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE, normalized_wav_path=str(wav), duration_sec=2.0,
    )

    with patch("pipeline.transcription.whisper_transcribe", return_value=_result()):
        transcribe_job(str(job.id))
        transcribe_job(str(job.id))

    assert Transcript.objects.filter(job=job).count() == 1


def test_transcribe_job_raises_when_no_normalized_wav():
    job = Job.objects.create(source_type=SourceType.FILE, normalized_wav_path=None)
    with pytest.raises(TranscriptionError) as exc:
        transcribe_job(str(job.id))
    assert exc.value.code == "TRANSCRIPTION_NO_SOURCE"


def test_transcribe_job_raises_on_empty_transcript(tmp_path: Path):
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE, normalized_wav_path=str(wav), duration_sec=2.0,
    )
    with patch(
        "pipeline.transcription.whisper_transcribe",
        return_value=_result(text="   ", segments=[]),
    ):
        with pytest.raises(TranscriptionError) as exc:
            transcribe_job(str(job.id))
    assert exc.value.code == "TRANSCRIPTION_EMPTY"


def test_transcribe_job_raises_on_unsupported_language(tmp_path: Path):
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE, normalized_wav_path=str(wav), duration_sec=2.0,
    )
    with patch(
        "pipeline.transcription.whisper_transcribe",
        return_value=_result(language="ru", text="Привет"),
    ):
        with pytest.raises(TranscriptionError) as exc:
            transcribe_job(str(job.id))
    assert exc.value.code == "TRANSCRIPTION_UNSUPPORTED_LANGUAGE"


def test_transcribe_job_raises_on_likely_noise(tmp_path: Path):
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE, normalized_wav_path=str(wav), duration_sec=60.0,
    )
    noisy = _result(
        text="thank you for watching " * 6,
        segments=[
            {"id": i, "start": float(i), "end": float(i + 1),
             "text": "thank you for watching", "words": []}
            for i in range(6)
        ],
    )
    with patch("pipeline.transcription.whisper_transcribe", return_value=noisy):
        with pytest.raises(TranscriptionError) as exc:
            transcribe_job(str(job.id))
    assert exc.value.code == "TRANSCRIPTION_LIKELY_NOISE"


def test_transcribe_job_maps_transient_whisper_error_to_service_down(tmp_path: Path):
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE, normalized_wav_path=str(wav), duration_sec=2.0,
    )
    with patch(
        "pipeline.transcription.whisper_transcribe",
        side_effect=WhisperError("retries exhausted", transient=True),
    ):
        with pytest.raises(TranscriptionError) as exc:
            transcribe_job(str(job.id))
    assert exc.value.code == "TRANSCRIPTION_SERVICE_DOWN"


def test_transcribe_job_maps_permanent_whisper_error_to_invalid_input(tmp_path: Path):
    wav = tmp_path / "normalized.wav"
    wav.write_bytes(b"\x00" * 1024)
    job = Job.objects.create(
        source_type=SourceType.FILE, normalized_wav_path=str(wav), duration_sec=2.0,
    )
    with patch(
        "pipeline.transcription.whisper_transcribe",
        side_effect=WhisperError("corrupt file", transient=False),
    ):
        with pytest.raises(TranscriptionError) as exc:
            transcribe_job(str(job.id))
    assert exc.value.code == "TRANSCRIPTION_INVALID_INPUT"
