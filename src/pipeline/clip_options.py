"""Per-job clip rendering options (Pro): 9:16 layout + caption style.

Parsed from the upload / from_url request, stored on ``Job``, consumed by
``workers.video_clip_worker`` → ``pipeline.ffmpeg_clip`` / ``ass_subtitles``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from api.errors import ClipOptionsInvalid

# SPEC §5.4 step 2: "pad (black bars) or crop=1080:1920 for center-crop —
# chosen via ENV flag". Pro makes it a per-job choice.
LAYOUT_PAD = "pad"
LAYOUT_CROP = "crop"
CLIP_LAYOUTS: tuple[str, ...] = (LAYOUT_PAD, LAYOUT_CROP)
DEFAULT_CLIP_LAYOUT = LAYOUT_PAD

CAPTION_KARAOKE = "karaoke"
CAPTION_CLEAN = "clean"
CAPTION_BOXED = "boxed"
CAPTION_STYLES: tuple[str, ...] = (CAPTION_KARAOKE, CAPTION_CLEAN, CAPTION_BOXED)
DEFAULT_CAPTION_STYLE = CAPTION_KARAOKE

MAX_HINT_LEN = 300


@dataclass(frozen=True)
class ClipOptions:
    layout: str = DEFAULT_CLIP_LAYOUT
    caption_style: str = DEFAULT_CAPTION_STYLE


def parse_clip_options(data: Mapping[str, Any]) -> ClipOptions:
    """Validate ``clip_layout`` / ``caption_style``; both optional."""
    raw_layout = data.get("clip_layout")
    layout = str(raw_layout).strip().lower() if raw_layout else DEFAULT_CLIP_LAYOUT
    if layout not in CLIP_LAYOUTS:
        raise ClipOptionsInvalid(field="clip_layout", detail=f"one of {', '.join(CLIP_LAYOUTS)}")

    raw_style = data.get("caption_style")
    style = str(raw_style).strip().lower() if raw_style else DEFAULT_CAPTION_STYLE
    if style not in CAPTION_STYLES:
        raise ClipOptionsInvalid(field="caption_style", detail=f"one of {', '.join(CAPTION_STYLES)}")

    return ClipOptions(layout=layout, caption_style=style)


def clip_options_for_job(job: Any) -> ClipOptions:
    """Read the stored options back with safe defaults for legacy rows."""
    layout = getattr(job, "clip_layout", None) or DEFAULT_CLIP_LAYOUT
    style = getattr(job, "caption_style", None) or DEFAULT_CAPTION_STYLE
    return ClipOptions(
        layout=layout if layout in CLIP_LAYOUTS else DEFAULT_CLIP_LAYOUT,
        caption_style=style if style in CAPTION_STYLES else DEFAULT_CAPTION_STYLE,
    )


def clean_hint(raw: Any) -> str | None:
    """Normalise a regenerate hint: strip, cap length, empty → ``None``."""
    if raw is None:
        return None
    hint = str(raw).strip()
    if not hint:
        return None
    if len(hint) > MAX_HINT_LEN:
        raise ClipOptionsInvalid(field="hint", detail=f"max {MAX_HINT_LEN} characters")
    return hint
