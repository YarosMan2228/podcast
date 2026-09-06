"""Per-job branding (Pro): podcast name, accent colour, logo.

Parsed once from the upload / from_url request, stored on the ``Job`` row,
and rendered by the quote-graphic and thumbnail workers. SPEC §7.1 US-7.3
wanted "set the logo once, see it everywhere" — this is that, scoped per
job until there are user accounts.
"""
from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from api.errors import BrandingInvalid

DEFAULT_BRAND_COLOR = "#6366f1"
DEFAULT_PODCAST_NAME = "Podcast Pack"
MAX_PODCAST_NAME_LEN = 120
MAX_LOGO_BYTES = 2 * 1024 * 1024

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# No SVG: it is served back from /media on the app origin and can carry
# <script> — a stored XSS vector. Raster formats are verified with Pillow.
_LOGO_MIMES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
_PIL_FORMAT_TO_MIME: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
MAX_LOGO_PIXELS = 4096 * 4096


def _verify_logo_image(logo: UploadedFile) -> str:
    """Return the *actual* MIME by decoding the bytes with Pillow.

    The browser-declared content type and the extension are both
    attacker-controlled; decoding is the only check that means anything.
    Raises ``BrandingInvalid`` for non-images, disallowed formats, or
    decompression-bomb sized dimensions.
    """
    from PIL import Image, UnidentifiedImageError

    logo.seek(0)
    try:
        with Image.open(logo) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BrandingInvalid(field="logo", detail="not a readable image") from exc
    finally:
        logo.seek(0)
    mime = _PIL_FORMAT_TO_MIME.get(fmt)
    if mime is None:
        raise BrandingInvalid(field="logo", detail="PNG, JPEG or WebP only")
    if width * height > MAX_LOGO_PIXELS:
        raise BrandingInvalid(field="logo", detail="image too large (max 4096x4096)")
    return mime


@dataclass(frozen=True)
class Branding:
    podcast_name: str | None = None
    brand_color: str = DEFAULT_BRAND_COLOR
    logo: UploadedFile | None = None


def parse_branding(data: Mapping[str, Any], files: Mapping[str, Any]) -> Branding:
    """Validate branding fields from a request; raise ``BrandingInvalid`` on bad input.

    All fields are optional. Works for both JSON bodies (no logo possible)
    and multipart bodies.
    """
    raw_name = data.get("podcast_name")
    name = str(raw_name).strip() if raw_name is not None else ""
    if len(name) > MAX_PODCAST_NAME_LEN:
        raise BrandingInvalid(
            field="podcast_name", detail=f"max {MAX_PODCAST_NAME_LEN} characters"
        )

    raw_color = data.get("brand_color")
    color = str(raw_color).strip() if raw_color else DEFAULT_BRAND_COLOR
    if not _HEX_COLOR.match(color):
        raise BrandingInvalid(field="brand_color", detail="expected #RRGGBB")

    logo = files.get("logo") if files else None
    if logo is not None:
        if logo.size == 0:
            raise BrandingInvalid(field="logo", detail="file is empty")
        if logo.size > MAX_LOGO_BYTES:
            raise BrandingInvalid(field="logo", detail="max 2 MB")
        # Content decides, not the header or the extension (checklist #14).
        logo.content_type = _verify_logo_image(logo)

    return Branding(podcast_name=name or None, brand_color=color.lower(), logo=logo)


def store_logo(job_id: str, logo: UploadedFile) -> str:
    """Write the logo under ``uploads/<job_id>/logo.<ext>``; return the relative path."""
    ext = _LOGO_MIMES.get((logo.content_type or "").lower(), "png")
    rel = Path("uploads") / str(job_id) / f"logo.{ext}"
    dest = Path(settings.MEDIA_ROOT) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        for chunk in logo.chunks():
            fh.write(chunk)
    return rel.as_posix()


def logo_data_uri(rel_path: str | None) -> str | None:
    """Inline the logo as ``data:`` so Playwright's ``set_content`` can show it.

    Chromium refuses ``file://`` images inside an about:blank page, and we
    don't want the renderer to depend on a running web server.
    """
    if not rel_path:
        return None
    p = Path(rel_path)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / p
    if not p.exists():
        return None
    mime, _ = mimetypes.guess_type(str(p))
    if not mime:
        mime = "image/png"
    payload = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def branding_for_job(job: Any) -> dict[str, Any]:
    """Render-ready branding dict for the graphics workers."""
    color = getattr(job, "brand_color", None) or DEFAULT_BRAND_COLOR
    if not _HEX_COLOR.match(color):
        color = DEFAULT_BRAND_COLOR
    return {
        "podcast_name": getattr(job, "podcast_name", None) or DEFAULT_PODCAST_NAME,
        "brand_color": color,
        "logo_data_uri": logo_data_uri(getattr(job, "logo_path", None)),
    }
