"""pipeline.ffmpeg_clip — vertical 9:16 ffmpeg runner (SPEC §5.4).

subprocess itself is mocked: we assert the command shape, error mapping,
and filesystem side-effects, not ffmpeg behaviour.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.ffmpeg_clip import (
    CLIP_HEAD_PAD_SEC,
    FFmpegClipError,
    build_clip_command,
    build_vertical_clip,
)


# ---------- helpers ----------


def _ok(returncode: int = 0, stderr: str = "") -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = ""
    cp.stderr = stderr
    return cp


# ---------- build_clip_command ----------


def test_build_clip_command_rejects_inverted_range() -> None:
    with pytest.raises(FFmpegClipError, match="CLIP_INVALID_RANGE"):
        build_clip_command("in.mp4", 5000, 4000, None, "out.mp4")


def test_build_clip_command_shape_for_video_source() -> None:
    cmd = build_clip_command(
        "raw.mp4", start_ms=10_000, end_ms=40_000, ass_path=None, output_path="clip.mp4"
    )
    # Standard encoder knobs present.
    for flag in ("ffmpeg", "-y", "-i", "raw.mp4", "-vf", "libx264", "clip.mp4"):
        assert flag in cmd
    # Fast seek = start_ms/1000 - head pad (10 - 1 = 9s).
    fast_seek = cmd[cmd.index("-ss") + 1]
    assert abs(float(fast_seek) - (10.0 - CLIP_HEAD_PAD_SEC)) < 1e-3
    # Video filter has pad to 1080x1920 and no subtitles (ass_path was None).
    vf = cmd[cmd.index("-vf") + 1]
    assert "1080" in vf and "1920" in vf
    assert "subtitles" not in vf


def test_build_clip_command_injects_subtitles_filter_when_ass_present() -> None:
    cmd = build_clip_command(
        "raw.mp4", 0, 30_000, ass_path="/tmp/sub.ass", output_path="clip.mp4"
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "subtitles='/tmp/sub.ass'" in vf


def test_build_clip_command_audio_only_uses_filter_complex() -> None:
    cmd = build_clip_command(
        "song.mp3", 0, 30_000, ass_path=None, output_path="clip.mp4", audio_only=True
    )
    # Audio-only path doesn't use -vf; it uses -filter_complex + map.
    assert "-vf" not in cmd
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "showwaves" in fc
    # Maps the generated video stream + the original audio.
    maps = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-map"]
    assert "[v]" in maps and "0:a" in maps


def test_build_clip_command_clamps_seek_to_zero() -> None:
    """Head pad would send seek negative for a clip starting at 0ms —
    ffmpeg accepts 0 but not a negative ``-ss``."""
    cmd = build_clip_command("raw.mp4", 0, 30_000, None, "clip.mp4")
    first_seek = cmd[cmd.index("-ss") + 1]
    assert float(first_seek) >= 0.0


# ---------- build_vertical_clip (runner) ----------


def test_build_vertical_clip_success(tmp_path: Path) -> None:
    """Happy path: subprocess returns 0 and output file is big enough."""
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"\x00" * 8192)  # pre-create so size check passes

    with patch("pipeline.ffmpeg_clip.subprocess.run", return_value=_ok()) as run:
        build_vertical_clip(
            input_media_path=str(tmp_path / "in.mp4"),
            start_ms=1000,
            end_ms=30_000,
            ass_path=None,
            output_path=str(output),
        )
    run.assert_called_once()
    # First arg to run is the command list — sanity check it's list-shaped.
    called_cmd = run.call_args.args[0]
    assert isinstance(called_cmd, list) and called_cmd[0] == "ffmpeg"


def test_build_vertical_clip_ffmpeg_nonzero_raises(tmp_path: Path) -> None:
    with patch(
        "pipeline.ffmpeg_clip.subprocess.run",
        return_value=_ok(returncode=1, stderr="invalid data"),
    ):
        with pytest.raises(FFmpegClipError, match="CLIP_FFMPEG_FAILED"):
            build_vertical_clip(
                str(tmp_path / "in.mp4"), 0, 30_000, None, str(tmp_path / "out.mp4")
            )


def test_build_vertical_clip_timeout_raises(tmp_path: Path) -> None:
    with patch(
        "pipeline.ffmpeg_clip.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=300),
    ):
        with pytest.raises(FFmpegClipError, match="CLIP_FFMPEG_TIMEOUT"):
            build_vertical_clip(
                str(tmp_path / "in.mp4"), 0, 30_000, None, str(tmp_path / "out.mp4")
            )


def test_build_vertical_clip_missing_binary_raises(tmp_path: Path) -> None:
    with patch("pipeline.ffmpeg_clip.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(FFmpegClipError, match="CLIP_FFMPEG_MISSING"):
            build_vertical_clip(
                str(tmp_path / "in.mp4"), 0, 30_000, None, str(tmp_path / "out.mp4")
            )


def test_build_vertical_clip_rejects_empty_output(tmp_path: Path) -> None:
    """Subprocess succeeded but output never appeared or is suspiciously small."""
    output = tmp_path / "empty.mp4"
    output.write_bytes(b"\x00" * 10)  # below _MIN_OUTPUT_BYTES (4096)

    with patch("pipeline.ffmpeg_clip.subprocess.run", return_value=_ok()):
        with pytest.raises(FFmpegClipError, match="too small"):
            build_vertical_clip(
                str(tmp_path / "in.mp4"), 0, 30_000, None, str(output)
            )
