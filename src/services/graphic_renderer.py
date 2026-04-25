"""Playwright HTML → PNG renderer for quote graphics — SPEC §7.3.

``render_quote_to_png`` is the single public entry point. It reads a
template file, fills in the three placeholders, then drives a headless
Chromium browser to produce a 1080×1080 PNG.

Font-size is auto-selected: quotes longer than LONG_QUOTE_THRESHOLD chars
get a smaller size so the text still fits inside the card boundaries.

Template placeholders:
    {{QUOTE}}        — HTML-escaped quote text
    {{SPEAKER}}      — HTML-escaped speaker name
    {{PODCAST_NAME}} — hardcoded brand string (MVP; will be a setting in v2)
    {{FONT_SIZE}}    — integer px value injected before rendering
"""
from __future__ import annotations

import asyncio
import html as _html_module
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PODCAST_NAME = "Podcast Pack"

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "frontend"
    / "src"
    / "quote_templates"
)

FONT_SIZE_NORMAL = 56
FONT_SIZE_SMALL = 48
LONG_QUOTE_THRESHOLD = 120

TEMPLATE_FILES: dict[str, str] = {
    "minimal_dark": "minimal_dark.html",
    "gradient_purple": "gradient_purple.html",
}


def _font_size(quote: str) -> int:
    return FONT_SIZE_SMALL if len(quote) > LONG_QUOTE_THRESHOLD else FONT_SIZE_NORMAL


def _fill_template(template_id: str, quote: str, speaker: str) -> str:
    filename = TEMPLATE_FILES.get(template_id, "minimal_dark.html")
    template_path = TEMPLATES_DIR / filename
    template = template_path.read_text(encoding="utf-8")
    return (
        template.replace("{{QUOTE}}", _html_module.escape(quote))
        .replace("{{SPEAKER}}", _html_module.escape(speaker))
        .replace("{{PODCAST_NAME}}", _html_module.escape(PODCAST_NAME))
        .replace("{{FONT_SIZE}}", str(_font_size(quote)))
    )


async def _render_async(html_content: str, output_path: Path) -> None:
    from playwright.async_api import async_playwright  # type: ignore

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1080, "height": 1080})
            await page.set_content(html_content, wait_until="domcontentloaded")
            await page.screenshot(path=str(output_path), full_page=False)
        finally:
            await browser.close()


def render_quote_to_png(
    quote: str,
    speaker: str,
    output_path: Path,
    *,
    template_id: str = "minimal_dark",
) -> None:
    """Render a 1080×1080 quote card PNG via headless Chromium.

    Args:
        quote: Quote text (20–180 chars recommended).
        speaker: Attribution name.
        output_path: Absolute path for the output PNG (parent dirs created).
        template_id: One of ``minimal_dark``, ``gradient_purple``.

    Raises:
        RuntimeError: If Playwright fails or the template file is missing.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_content = _fill_template(template_id, quote, speaker)

    try:
        asyncio.run(_render_async(html_content, output_path))
    except Exception as exc:
        raise RuntimeError(f"Playwright render failed for {template_id!r}: {exc}") from exc

    logger.info(
        "quote_rendered",
        extra={
            "template_id": template_id,
            "quote_len": len(quote),
            "output_path": str(output_path),
        },
    )
