"""pipeline.ingestion — ffmpeg/ffprobe wrappers and ingest_job orchestration.

The subprocess calls themselves are mocked: we're testing the contract
(command shape, error mapping, DB side-effects), not ffmpeg itself.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from jobs.models import Job, SourceType
from pipeline.ingestion import (
    FFMPEG_TIMEOUT_SEC,
    FFPROBE_TIMEOUT_SEC,
    IngestionError,
    ingest_job,
    normalize_to_wav,
    probe_duration_sec,
)

pytestmark = pytest.mark.django_db


# ---------- helpers ----------


def _ok(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _make_wav_file(path: Path, size: int = 4096) -> None:
    """Write a dummy file big enough to pass normalize_to_wav's size gate (>=1024)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)


# ---------- normalize_to_wav ----------


def test_normalize_to_wav_builds_spec_command(tmp_path: Path) -> None:
    src = tmp_path / "in.mp3"
    src.write_bytes(b"\x00" * 16)
    dst = tmp_path / "normalized.wav"

    def _fake_run(cmd, **kwargs):  # noqa: ANN001 - subprocess signature
        # Simulate ffmpeg producing an output file.
        _make_wav_file(Path(cmd[-1]))
        return _ok()

    with patch("pipeline.ingestion.subprocess.run", side_effect=_fake_run) as run:
        normalize_to_wav(str(src), str(dst))

    cmd = run.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    # SPEC §2.4 exact flags: mono, 16kHz, pcm_s16le, overwrite.
    assert "-y" in cmd
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert cmd[-1] == str(dst)
    assert run.call_args.kwargs["timeout"] == FFMPEG_TIMEOUT_SEC
    assert run.call_args.kwargs.get("shell") is not True  # list form, not shell


def test_normalize_to_wav_raises_on_nonzero_returncode(tmp_path: Path) -> None:
    src = tmp_path / "in.mp3"
    src.write_bytes(b"\x00")
    dst = tmp_path / "normalized.wav"
    with patch(
        "pipeline.ingestion.subprocess.run",
        return_value=_ok(stderr="Invalid data found when processing input", returncode=1),
    ):
        with pytest.raises(IngestionError) as exc:
            normalize_to_wav(str(src), str(dst))
    assert exc.value.code == "INGESTION_NORMALIZE_FAILED"
    assert "exited 1" in exc.value.message


def test_normalize_to_wav_raises_when_ffmpeg_missing(tmp_path: Path) -> None:
    with patch("pipeline.ingestion.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(IngestionError) as exc:
            normalize_to_wav(str(tmp_path / "in.mp3"), str(tmp_path / "out.wav"))
    assert exc.value.code == "INGESTION_NORMALIZE_FAILED"
    assert "not found" in exc.value.message.lower()


def test_normalize_to_wav_raises_on_timeout(tmp_path: Path) -> None:
    with patch(
        "pipeline.ingestion.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=FFMPEG_TIMEOUT_SEC),
    ):
        with pytest.raises(IngestionError) as exc:
            normalize_to_wav(str(tmp_path / "in.mp3"), str(tmp_path / "out.wav"))
    assert exc.value.code == "INGESTION_NORMALIZE_FAILED"


def test_normalize_to_wav_raises_when_output_too_small(tmp_path: Path) -> None:
    """A returncode-0 ffmpeg that produced a <1 KB file is still a failure."""
    src = tmp_path / "in.mp3"
    src.write_bytes(b"\x00")
    dst = tmp_path / "out.wav"

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        Path(cmd[-1]).write_bytes(b"tiny")  # 4 bytes << 1024
        return _ok()

    with patch("pipeline.ingestion.subprocess.run", side_effect=_fake_run):
        with pytest.raises(IngestionError) as exc:
            normalize_to_wav(str(src), str(dst))
    assert exc.value.code == "INGESTION_NORMALIZE_FAILED"
    assert "too small" in exc.value.message


# ---------- probe_duration_sec ----------


def test_probe_duration_sec_parses_float(tmp_path: Path) -> None:
    src = tmp_path / "in.wav"
    src.write_bytes(b"\x00" * 2048)
    with patch(
        "pipeline.ingestion.subprocess.run",
        return_value=_ok(stdout="3612.5\n"),
    ) as run:
        duration = probe_duration_sec(str(src))

    assert duration == pytest.approx(3612.5)
    cmd = run.call_args.args[0]
    assert cmd[0] == "ffprobe"
    assert "format=duration" in cmd
    assert run.call_args.kwargs["timeout"] == FFPROBE_TIMEOUT_SEC


def test_probe_duration_sec_rejects_non_numeric_output(tmp_path: Path) -> None:
    with patch(
        "pipeline.ingestion.subprocess.run",
        return_value=_ok(stdout="N/A\n"),
    ):
        with pytest.raises(IngestionError) as exc:
            probe_duration_sec(str(tmp_path / "x.wav"))
    assert exc.value.code == "INGESTION_DURATION_UNKNOWN"


def test_probe_duration_sec_rejects_empty_output(tmp_path: Path) -> None:
    with patch(
        "pipeline.ingestion.subprocess.run",
        return_value=_ok(stdout=""),
    ):
        with pytest.raises(IngestionError) as exc:
            probe_duration_sec(str(tmp_path / "x.wav"))
    assert exc.value.code == "INGESTION_DURATION_UNKNOWN"


def test_probe_duration_sec_rejects_nonpositive(tmp_path: Path) -> None:
    with patch(
        "pipeline.ingestion.subprocess.run",
        return_value=_ok(stdout="0.0\n"),
    ):
        with pytest.raises(IngestionError) as exc:
            probe_duration_sec(str(tmp_path / "x.wav"))
    assert exc.value.code == "INGESTION_DURATION_UNKNOWN"


def test_probe_duration_sec_raises_on_ffprobe_error(tmp_path: Path) -> None:
    with patch(
        "pipeline.ingestion.subprocess.run",
        return_value=_ok(stderr="moov atom not found", returncode=1),
    ):
        with pytest.raises(IngestionError) as exc:
            probe_duration_sec(str(tmp_path / "x.wav"))
    assert exc.value.code == "INGESTION_DURATION_UNKNOWN"


def test_probe_duration_sec_raises_when_ffprobe_missing(tmp_path: Path) -> None:
    with patch("pipeline.ingestion.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(IngestionError) as exc:
            probe_duration_sec(str(tmp_path / "x.wav"))
    assert exc.value.code == "INGESTION_DURATION_UNKNOWN"


# ---------- ingest_job ----------


def test_ingest_job_populates_duration_and_normalized_path(tmp_path: Path) -> None:
    raw = tmp_path / "uploads" / "job" / "ep.mp3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"\x00" * 32)
    job = Job.objects.create(
        source_type=SourceType.FILE,
        raw_media_path=str(raw),
        original_filename="ep.mp3",
    )

    with patch("pipeline.ingestion.normalize_to_wav") as nrm, patch(
        "pipeline.ingestion.probe_duration_sec", return_value=61.2
    ):
        nrm.side_effect = lambda inp, out: _make_wav_file(Path(out))
        ingest_job(str(job.id))

    job.refresh_from_db()
    assert job.duration_sec == pytest.approx(61.2)
    expected = raw.parent / "normalized.wav"
    assert job.normalized_wav_path == str(expected)
    # normalize_to_wav called with raw path and the adjacent normalized.wav.
    args, _ = nrm.call_args
    assert args == (str(raw), str(expected))


def test_ingest_job_raises_when_no_raw_media_path() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, raw_media_path=None)
    with pytest.raises(IngestionError) as exc:
        ingest_job(str(job.id))
    assert exc.value.code == "INGESTION_NO_SOURCE"


def test_ingest_job_raises_when_raw_media_missing(tmp_path: Path) -> None:
    job = Job.objects.create(
        source_type=SourceType.FILE,
        raw_media_path=str(tmp_path / "does_not_exist.mp3"),
    )
    with pytest.raises(IngestionError) as exc:
        ingest_job(str(job.id))
    assert exc.value.code == "INGESTION_NO_SOURCE"


@override_settings(MAX_EPISODE_DURATION_MIN=1)  # 1 minute cap
def test_ingest_job_enforces_episode_duration_cap(tmp_path: Path) -> None:
    """SPEC §2.4: duration > MAX_EPISODE_DURATION_MIN → INGESTION_EPISODE_TOO_LONG."""
    raw = tmp_path / "uploads" / "job" / "ep.mp3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"\x00" * 16)
    job = Job.objects.create(source_type=SourceType.FILE, raw_media_path=str(raw))

    with patch("pipeline.ingestion.normalize_to_wav") as nrm, patch(
        "pipeline.ingestion.probe_duration_sec", return_value=120.0
    ):
        nrm.side_effect = lambda inp, out: _make_wav_file(Path(out))
        with pytest.raises(IngestionError) as exc:
            ingest_job(str(job.id))
    assert exc.value.code == "INGESTION_EPISODE_TOO_LONG"

    # Job row must NOT have been partially updated on failure.
    job.refresh_from_db()
    assert job.duration_sec is None
    assert job.normalized_wav_path is None


def test_ingest_job_propagates_normalize_failure(tmp_path: Path) -> None:
    raw = tmp_path / "uploads" / "job" / "ep.mp3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"\x00" * 16)
    job = Job.objects.create(source_type=SourceType.FILE, raw_media_path=str(raw))

    with patch(
        "pipeline.ingestion.normalize_to_wav",
        side_effect=IngestionError("INGESTION_NORMALIZE_FAILED", "ffmpeg exited 1"),
    ):
        with pytest.raises(IngestionError) as exc:
            ingest_job(str(job.id))
    assert exc.value.code == "INGESTION_NORMALIZE_FAILED"
