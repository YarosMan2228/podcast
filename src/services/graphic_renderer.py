"""Playwright HTML → PNG renderer for quote graphics + thumbnails — SPEC §7.3.

Two public entry points:

* ``render_quote_to_png`` — 1080×1080 quote card (SPEC §7).
* ``render_thumbnail_to_png`` — 1280×720 episode thumbnail (Pro).

Both fill an HTML template from ``frontend/src/quote_templates`` and drive
headless Chromium via ``render_html_to_png``.

Template placeholders:
    {{QUOTE}} / {{SPEAKER}}          — HTML-escaped (quote cards)
    {{TITLE}} / {{HOOK}}             — HTML-escaped (thumbnail)
    {{PODCAST_NAME}}                 — HTML-escaped brand string
    {{BRAND_COLOR}}                  — ``#rrggbb`` accent (validated upstream)
    {{LOGO_IMG}}                     — ``<img …>`` tag or empty string
    {{FONT_SIZE}} / {{TITLE_SIZE}}   — integer px, auto-selected by text length
"""
from __future__ import annotations

import asyncio
import html as _html_module
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PODCAST_NAME = "Podcast Pack"
DEFAULT_BRAND_COLOR = "#6366f1"

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "frontend"
    / "src"
    / "quote_templates"
)

FONT_SIZE_NORMAL = 56
FONT_SIZE_SMALL = 48
LONG_QUOTE_THRESHOLD = 120

TITLE_SIZE_NORMAL = 84
TITLE_SIZE_SMALL = 64
LONG_TITLE_THRESHOLD = 36

TEMPLATE_FILES: dict[str, str] = {
    "minimal_dark": "minimal_dark.html",
    "gradient_purple": "gradient_purple.html",
    "thumbnail_default": "thumbnail_default.html",
}

QUOTE_SIZE = (1080, 1080)
THUMBNAIL_SIZE = (1280, 720)


def _font_size(quote: str) -> int:
    return FONT_SIZE_SMALL if len(quote) > LONG_QUOTE_THRESHOLD else FONT_SIZE_NORMAL


def _title_size(title: str) -> int:
    return TITLE_SIZE_SMALL if len(title) > LONG_TITLE_THRESHOLD else TITLE_SIZE_NORMAL


def _logo_img(data_uri: str | None, css_class: str = "logo") -> str:
    if not data_uri:
        return ""
    # data: URIs never contain quotes/angle brackets, but escape anyway.
    return f'<img class="{css_class}" src="{_html_module.escape(data_uri)}" alt="">'


def _brand_fields(branding: dict[str, Any] | None) -> dict[str, str]:
    b = branding or {}
    return {
        "{{PODCAST_NAME}}": _html_module.escape(b.get("podcast_name") or PODCAST_NAME),
        "{{BRAND_COLOR}}": b.get("brand_color") or DEFAULT_BRAND_COLOR,
        "{{LOGO_IMG}}": _logo_img(b.get("logo_data_uri")),
    }


def _read_template(template_id: str, fallback: str) -> str:
    filename = TEMPLATE_FILES.get(template_id, fallback)
    return (TEMPLATES_DIR / filename).read_text(encoding="utf-8")


def _apply(template: str, fields: dict[str, str]) -> str:
    for key, value in fields.items():
        template = template.replace(key, value)
    return template


def _fill_template(
    template_id: str,
    quote: str,
    speaker: str,
    branding: dict[str, Any] | None = None,
) -> str:
    template = _read_template(template_id, "minimal_dark.html")
    fields = {
        "{{QUOTE}}": _html_module.escape(quote),
        "{{SPEAKER}}": _html_module.escape(speaker),
        "{{FONT_SIZE}}": str(_font_size(quote)),
        **_brand_fields(branding),
    }
    return _apply(template, fields)


def _fill_thumbnail_template(
    template_id: str,
    title: str,
    hook: str,
    branding: dict[str, Any] | None = None,
) -> str:
    template = _read_template(template_id, "thumbnail_default.html")
    fields = {
        "{{TITLE}}": _html_module.escape(title),
        "{{HOOK}}": _html_module.escape(hook),
        "{{TITLE_SIZE}}": str(_title_size(title)),
        **_brand_fields(branding),
    }
    return _apply(template, fields)


async def _render_async(html_content: str, output_path: Path, size: tuple[int, int]) -> None:
    from playwright.async_api import async_playwright  # type: ignore

    width, height = size
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.set_content(html_content, wait_until="domcontentloaded")
            await page.screenshot(path=str(output_path), full_page=False)
        finally:
            await browser.close()


def render_html_to_png(
    html_content: str, output_path: Path, *, size: tuple[int, int]
) -> None:
    """Screenshot *html_content* at *size* into *output_path* (parent dirs created)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_render_async(html_content, output_path, size))
    except ImportError:
        # Playwright not installed — permanent, don't wrap into the
        # transient RuntimeError bucket (workers would retry pointlessly).
        raise
    except Exception as exc:
        raise RuntimeError(f"Playwright render failed: {exc}") from exc


def render_quote_to_png(
    quote: str,
    speaker: str,
    output_path: Path,
    *,
    template_id: str = "minimal_dark",
    branding: dict[str, Any] | None = None,
) -> None:
    """Render a 1080×1080 quote card PNG via headless Chromium.

    Args:
        quote: Quote text (20–180 chars recommended).
        speaker: Attribution name.
        output_path: Absolute path for the output PNG (parent dirs created).
        template_id: One of ``minimal_dark``, ``gradient_purple``.
        branding: ``{"podcast_name", "brand_color", "logo_data_uri"}`` — see
            ``pipeline.branding.branding_for_job``.

    Raises:
        RuntimeError: If Playwright fails or the template file is missing.
    """
    html_content = _fill_template(template_id, quote, speaker, branding)
    render_html_to_png(html_content, output_path, size=QUOTE_SIZE)
    logger.info(
        "quote_rendered",
        extra={
            "template_id": template_id,
            "quote_len": len(quote),
            "output_path": str(output_path),
        },
    )


def render_thumbnail_to_png(
    title: str,
    hook: str,
    output_path: Path,
    *,
    template_id: str = "thumbnail_default",
    branding: dict[str, Any] | None = None,
) -> None:
    """Render a 1280×720 episode thumbnail (YouTube / podcast cover)."""
    html_content = _fill_thumbnail_template(template_id, title, hook, branding)
    render_html_to_png(html_content, output_path, size=THUMBNAIL_SIZE)
    logger.info(
        "thumbnail_rendered",
        extra={"template_id": template_id, "title_len": len(title), "output_path": str(output_path)},
    )
