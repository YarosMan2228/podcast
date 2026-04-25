"""yt-dlp wrapper for URL-sourced jobs (SPEC §2.4).

Two responsibilities:

1. ``validate_url`` — synchronous host-whitelist check used from
   ``POST /api/jobs/from_url`` to reject bad input *before* a Job row is
   created.
2. ``download_from_url`` — invoked from the Celery ingestion task to pull
   the audio track to disk. Wraps ``yt_dlp.YoutubeDL`` so the rest of the
   pipeline can keep treating the result as a normal local media file.

MVP scope is **YouTube only**. Spotify / SoundCloud return
``URL_UNSUPPORTED_HOST`` per SPEC §2.4.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pipeline.ingestion import IngestionError

logger = logging.getLogger(__name__)


# SPEC §2.4 whitelist. Spotify/SoundCloud are intentionally excluded for
# MVP — they require yt-dlp plugins / DRM workarounds we won't ship now.
YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
)

# Hosts that we explicitly recognise as podcast platforms but won't support
# in MVP — kept separate so the error message can be specific.
KNOWN_UNSUPPORTED_HOSTS: frozenset[str] = frozenset(
    {"open.spotify.com", "spotify.com", "soundcloud.com", "www.soundcloud.com"}
)


class UrlValidationError(Exception):
    """Raised by :func:`validate_url` for non-http(s) or malformed URLs.

    The view catches this and converts to ``URL_INVALID``.
    """


class UnsupportedHostError(Exception):
    """Raised by :func:`validate_url` for hosts outside the whitelist.

    The view catches this and converts to ``URL_UNSUPPORTED_HOST``.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(host)


def validate_url(url: str | None) -> str:
    """Reject missing / non-http(s) / non-whitelisted URLs.

    Returns the URL unchanged on success so callers can use it directly.
    Raises :class:`UrlValidationError` or :class:`UnsupportedHostError`
    so the view can map each to the right status code (400 vs 400).
    """
    if not url or not isinstance(url, str):
        raise UrlValidationError("URL is required")

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UrlValidationError(f"Unsupported scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlValidationError("URL has no host")

    if host in YOUTUBE_HOSTS:
        return url
    # Surface specifically-known platforms with the same error code — the
    # message is what the frontend shows, the code is what it routes on.
    raise UnsupportedHostError(host)


def download_from_url(
    url: str,
    dest_dir: str | os.PathLike[str],
    *,
    ytdl_options: dict[str, Any] | None = None,
) -> Path:
    """Download *url* to *dest_dir* as an mp3, return path to the file.

    Uses ``yt_dlp.YoutubeDL`` (Python API, not subprocess) so we get a
    structured exception on failure instead of having to parse stderr.
    The downloaded file replaces ``raw_media_path`` for the Job and is
    fed straight into ``normalize_to_wav``.

    Raises :class:`IngestionError` (code ``URL_YTDLP_FAILED``) on any
    yt-dlp failure — live streams, geo-blocks, deleted videos, etc.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # ``%(id)s`` keeps the on-disk name deterministic from the YouTube id —
    # useful when re-running an ingestion after a transient failure.
    outtmpl = str(dest / "raw.%(ext)s")
    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Don't fall back to playlists — a single video URL must yield a
        # single file. If the user pastes a playlist URL we want the head
        # entry only.
        "noplaylist": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ],
    }
    if ytdl_options:
        options.update(ytdl_options)

    # Deferred import keeps the module importable in environments that
    # don't have yt-dlp installed (CI image without the URL feature).
    from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
    from yt_dlp.utils import DownloadError  # type: ignore[import-not-found]

    try:
        with YoutubeDL(options) as ydl:
            ydl.download([url])
    except DownloadError as exc:
        # yt-dlp's DownloadError contains a useful message ("This is a
        # live stream", "Video unavailable", etc.); pass it through as-is.
        logger.warning(
            "ytdlp_download_failed",
            extra={"url": url, "error": str(exc)},
        )
        raise IngestionError("URL_YTDLP_FAILED", str(exc)) from exc
    except OSError as exc:
        # Disk full, perms, etc.
        raise IngestionError(
            "URL_YTDLP_FAILED", f"yt-dlp IO error: {exc}"
        ) from exc

    # After the FFmpegExtractAudio postprocessor the resulting file ends
    # in .mp3 regardless of source format. Find it under dest_dir.
    candidates = sorted(dest.glob("raw.mp3"))
    if not candidates:
        # Fall back to any raw.* — yt-dlp may have skipped postprocessing
        # if ffmpeg is missing. Still usable; normalize_to_wav will catch
        # missing ffmpeg with a clear error.
        candidates = sorted(p for p in dest.glob("raw.*") if p.is_file())
    if not candidates:
        raise IngestionError(
            "URL_YTDLP_FAILED",
            "yt-dlp completed without producing an output file",
        )
    return candidates[0]
