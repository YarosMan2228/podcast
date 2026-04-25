"""ffmpeg command builder + runner for vertical 9:16 clip generation.

SPEC §5.4 / ``.claude/rules/ffmpeg-usage.md §3``. One public entry point
(:func:`build_vertical_clip`) that assembles a command list, runs it via
``subprocess.run`` with the same conventions as ``pipeline.ingestion``
(no shell, list args, bounded timeout, ``check=False`` + tail-logged
stderr), and raises :class:`FFmpegClipError` on any failure.

Two variants:

* **Video source** (default): scale + pad to 1080x1920, burn ASS captions,
  re-encode with H.264 baseline + AAC for mobile compatibility.
* **Audio-only source** (``audio_only=True``): generate a waveform-video
  via ``showwaves``, then burn captions on top.

The ASS path is optional — if ``ass_path`` is ``None`` we skip the
``subtitles=`` filter entirely (SPEC §5.5: "if no words in range → render
clip without subtitles, log warning"). The worker is responsible for
writing and deleting the temp .ass file.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# Matches ingestion.FFMPEG_TIMEOUT_SEC; a 60-sec clip should finish in
# under ~30 sec on veryfast preset, so 300 is a generous ceiling that
# aligns with the Celery ``soft_time_limit`` on the video queue.
FFMPEG_TIMEOUT_SEC = 300

# Pad around the clip so subtitles at word boundaries don't cut off and
# the cut has a little breathing room on either end. SPEC §5.4.
CLIP_HEAD_PAD_SEC = 1.0
CLIP_TAIL_PAD_SEC = 1.0

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

_STDERR_LOG_TAIL = 2000
_STDERR_EXC_TAIL = 500
_MIN_OUTPUT_BYTES = 4096  # smaller than this = ffmpeg bailed before writing a real mp4


class FFmpegClipError(Exception):
    """Pipeline-level failure rendering a video clip.

    ``code`` matches the naming scheme used by ingestion / analysis so
    the worker layer can persist + event-publish it without introspection.

    ``transient`` distinguishes input-data corruption / momentary IO glitches
    (worth one Celery retry per SPEC §5.5 "FFmpeg падает с Invalid data →
    Retry 1 раз") from permanent failures (missing binary, invalid range,
    output too small) that won't recover on a re-run.
    """

    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        self.code = code
        self.message = message
        self.transient = transient
        super().__init__(f"{code}: {message}")


# stderr substrings that indicate a transient ffmpeg failure (worth one retry).
# Kept narrow on purpose — false positives waste a retry slot; false negatives
# just mean the artifact gets marked FAILED on the first attempt as before.
_TRANSIENT_STDERR_MARKERS: tuple[str, ...] = (
    "Invalid data found when processing input",
    "Connection reset by peer",
    "Connection timed out",
    "Server returned 5",  # 5xx from a remote input URL
    "could not seek",
    "Resource temporarily unavailable",
)


def _is_transient_ffmpeg_failure(stderr: str) -> bool:
    """SPEC §5.5: certain ffmpeg failures recover on a second attempt."""
    if not stderr:
        return False
    return any(marker in stderr for marker in _TRANSIENT_STDERR_MARKERS)


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def _escape_ass_path_for_filter(path: str) -> str:
    """ffmpeg's ``subtitles=`` filter parses the path through a filtergraph
    parser: ``:`` and ``\\`` are structural. On Windows paths that's lethal;
    in our Linux container temp paths look like ``/tmp/sub_<hex>.ass`` and
    need no escaping, but we still normalise defensively so a dev-run on
    Windows doesn't silently render empty captions.
    """
    # Forward slashes only (Windows accepts them in ffmpeg arg contexts).
    p = path.replace("\\", "/")
    # Escape drive colons: ``C:/...`` → ``C\:/...``.
    if len(p) >= 2 and p[1] == ":":
        p = p[0] + r"\:" + p[2:]
    # Escape single quotes (they delimit the path inside the filter).
    return p.replace("'", r"\'")


def _build_video_filter(ass_path: str | None) -> str:
    """9:16 pad + burn-in subtitles (if any). Single ``-vf`` chain."""
    chain = [
        f"scale=w={OUTPUT_WIDTH}:h={OUTPUT_HEIGHT}"
        ":force_original_aspect_ratio=decrease",
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black",
    ]
    if ass_path:
        escaped = _escape_ass_path_for_filter(ass_path)
        chain.append(f"subtitles='{escaped}'")
    return ",".join(chain)


def _build_audio_filter_complex(ass_path: str | None) -> str:
    """Waveform video + captions for audio-only sources (SPEC §5.5).

    Layout: a 1080x1080 cline-mode waveform placed near the top of the
    1080x1920 canvas (y=420) over a black background; captions are then
    burned in via the subtitles filter chained onto the padded canvas.
    """
    base = (
        f"[0:a]showwaves=s={OUTPUT_WIDTH}x1080:mode=cline:colors=white,"
        f"format=yuv420p,pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:0:420:black"
    )
    if ass_path:
        escaped = _escape_ass_path_for_filter(ass_path)
        return f"{base}[bg];[bg]subtitles='{escaped}'[v]"
    return f"{base}[v]"


def build_clip_command(
    input_media_path: str,
    start_ms: int,
    end_ms: int,
    ass_path: str | None,
    output_path: str,
    *,
    audio_only: bool = False,
) -> list[str]:
    """Assemble the ffmpeg argv for a single clip.

    Exposed as a pure function so tests can assert the command shape
    without invoking ffmpeg. Not meant to be called directly by workers —
    use :func:`build_vertical_clip` which also runs the process.
    """
    if end_ms <= start_ms:
        raise FFmpegClipError(
            "CLIP_INVALID_RANGE",
            f"end_ms ({end_ms}) must be greater than start_ms ({start_ms})",
        )

    seek_sec = max(0.0, start_ms / 1000.0 - CLIP_HEAD_PAD_SEC)
    head_trim_sec = (start_ms / 1000.0) - seek_sec  # 0 if clip starts near 0
    duration_sec = (end_ms - start_ms) / 1000.0 + CLIP_TAIL_PAD_SEC + head_trim_sec

    cmd: list[str] = ["ffmpeg", "-y"]
    # Fast seek on input, then slow seek inside the decoded stream to hit
    # an exact frame boundary (SPEC §5.4). ``head_trim_sec`` is 0 when the
    # segment starts inside the first CLIP_HEAD_PAD_SEC of the episode.
    cmd += ["-ss", f"{seek_sec:.3f}", "-i", input_media_path]
    if head_trim_sec > 0:
        cmd += ["-ss", f"{head_trim_sec:.3f}"]
    cmd += ["-t", f"{duration_sec:.3f}"]

    if audio_only:
        cmd += ["-filter_complex", _build_audio_filter_complex(ass_path)]
        cmd += ["-map", "[v]", "-map", "0:a"]
    else:
        cmd += ["-vf", _build_video_filter(ass_path)]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


# ---------------------------------------------------------------------------
# Orchestration — runs the subprocess + checks the output
# ---------------------------------------------------------------------------


def build_vertical_clip(
    input_media_path: str,
    start_ms: int,
    end_ms: int,
    ass_path: str | None,
    output_path: str,
    *,
    audio_only: bool = False,
    job_id: str | None = None,
) -> None:
    """Render a single 9:16 clip from ``input_media_path`` to ``output_path``.

    Raises :class:`FFmpegClipError` on invalid ranges, ffmpeg non-zero
    exits, timeouts, or a missing/undersized output file. Success is
    signalled by simply returning — the caller then updates the Artifact
    row and emits the SSE event.

    ``job_id`` is logging-correlation only; pass it through from the worker
    so a failed-clip log entry can be greppped by job alongside the
    ingestion/transcription/analysis lines.
    """
    cmd = build_clip_command(
        input_media_path,
        start_ms,
        end_ms,
        ass_path,
        output_path,
        audio_only=audio_only,
    )

    # Ensure the output directory exists — ffmpeg would otherwise fail
    # with a confusing "No such file or directory" on the mp4 path itself.
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegClipError(
            "CLIP_FFMPEG_TIMEOUT",
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_SEC}s",
            transient=True,
        ) from exc
    except FileNotFoundError as exc:
        raise FFmpegClipError(
            "CLIP_FFMPEG_MISSING",
            "ffmpeg binary not found on PATH",
        ) from exc

    if result.returncode != 0:
        transient = _is_transient_ffmpeg_failure(result.stderr)
        logger.error(
            "ffmpeg_clip_failed",
            extra={
                "job_id": job_id,
                "cmd": " ".join(cmd),
                "stderr": result.stderr[-_STDERR_LOG_TAIL:],
                "returncode": result.returncode,
                "transient": transient,
            },
        )
        raise FFmpegClipError(
            "CLIP_FFMPEG_FAILED",
            f"ffmpeg exited {result.returncode}: "
            f"{result.stderr[-_STDERR_EXC_TAIL:]}",
            transient=transient,
        )

    out = Path(output_path)
    if not out.exists() or out.stat().st_size < _MIN_OUTPUT_BYTES:
        raise FFmpegClipError(
            "CLIP_FFMPEG_FAILED",
            f"Output missing or too small: {output_path}",
        )
