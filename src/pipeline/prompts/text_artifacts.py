"""Prompt templates for the 5 text artifact types — SPEC §6.4.

Each ``build_*`` function returns the *user-message* string for that artifact.
All prompts consume an ``analysis`` dict (from Analysis model) and a ``tone``
string. The transcript is NOT repeated here — it lives in the cached system
block inside ``services.claude_client.call_text_artifact``.

Allowed tones per SPEC §6.3:
    analytical  — thoughtful, evidence-based (default for LinkedIn, Show Notes)
    casual      — conversational, approachable (default for Twitter, Newsletter)
    punchy      — direct, short sentences, high energy
    professional — polished, authoritative, structured (default for YouTube Desc)
"""
from __future__ import annotations

import json

TONES = frozenset({"analytical", "casual", "punchy", "professional"})

DEFAULT_TONES: dict[str, str] = {
    "LINKEDIN_POST": "analytical",
    "TWITTER_THREAD": "casual",
    "SHOW_NOTES": "analytical",
    "NEWSLETTER": "casual",
    "YOUTUBE_DESCRIPTION": "professional",
}

_TONE_INSTRUCTIONS: dict[str, str] = {
    "analytical": (
        "Write in a thoughtful, analytical tone. Back up points with examples "
        "from the episode. Avoid fluff; every sentence should carry weight."
    ),
    "casual": (
        "Write in a conversational, approachable tone — like explaining to a "
        "smart friend over coffee. Contractions are fine. Be warm, not corporate."
    ),
    "punchy": (
        "Write in a punchy, direct tone. Short sentences. High energy. Cut every "
        "word that doesn't earn its place. Make the reader feel the urgency."
    ),
    "professional": (
        "Write in a professional, polished tone. Clear structure, authoritative "
        "voice, no jargon. Suitable for a business audience."
    ),
}


def _tone_instruction(tone: str) -> str:
    return _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS["analytical"])


def _format_quotes(quotes: list, limit: int = 5) -> str:
    return json.dumps(quotes[:limit], ensure_ascii=False)


def _format_chapters(chapters: list, limit: int = 8) -> str:
    return json.dumps(chapters[:limit], ensure_ascii=False)


def build_linkedin_prompt(analysis: dict, tone: str) -> str:
    guest_line = ""
    if analysis.get("guest_json"):
        guest_line = f"\nGuest: {json.dumps(analysis['guest_json'], ensure_ascii=False)}"

    return f"""<analysis>
Episode title: {analysis.get("episode_title", "")}
Hook: {analysis.get("hook", "")}
Themes: {", ".join(analysis.get("themes_json") or [])}
Key quotes: {_format_quotes(analysis.get("quotes_json") or [])}
{guest_line}
</analysis>

<tone_instruction>{_tone_instruction(tone)}</tone_instruction>

Write a LinkedIn post about this podcast episode that:
- Opens with a hook (a question, surprising claim, or contrarian take) in the first 1-2 lines — these show before "see more" so make them count
- Is 300–500 words total (HARD LIMIT: do not exceed 500 words)
- Uses short paragraphs (2–3 sentences each) with blank lines between them for readability
- Ends with either a question to drive engagement or a clear CTA to listen
- Appends 3–5 relevant hashtags at the very end, separated from the body by a blank line
- Does NOT use AI-detection markers: no "In conclusion", no "It's important to note", no "Firstly / Secondly", no bullet points in the body

Return ONLY the post text. No preamble, no markdown fences, no explanation."""


def build_twitter_prompt(analysis: dict, tone: str) -> str:
    return f"""<analysis>
Episode title: {analysis.get("episode_title", "")}
Hook: {analysis.get("hook", "")}
Themes: {", ".join(analysis.get("themes_json") or [])}
Key quotes: {_format_quotes(analysis.get("quotes_json") or [])}
Chapters: {_format_chapters(analysis.get("chapters_json") or [])}
</analysis>

<tone_instruction>{_tone_instruction(tone)}</tone_instruction>

Write a Twitter/X thread about this podcast episode with these STRICT rules:
- 6–10 tweets total
- Each tweet MUST be ≤ 270 characters (count carefully — this is a hard limit)
- Tweet 1 = the strongest hook: standalone, makes people want to read more
- Build a narrative arc: hook → key insights → surprising detail → conclusion
- Last tweet = CTA with the placeholder {{{{EPISODE_URL}}}}
- Do NOT include tweet numbering like "1/" or "🧵 1/8" in the text

Return ONLY valid JSON in this exact format — no preamble, no markdown fences, no extra keys:
{{"tweets": ["tweet 1 text here", "tweet 2 text here", ...]}}"""


def build_show_notes_prompt(analysis: dict, tone: str) -> str:
    guest_section_note = (
        "Include the '## About the guest' section with a 2-3 sentence bio."
        if analysis.get("guest_json")
        else "There is no guest info — skip the '## About the guest' section entirely."
    )
    guest_data = (
        f"\nGuest data: {json.dumps(analysis['guest_json'], ensure_ascii=False)}"
        if analysis.get("guest_json")
        else ""
    )

    return f"""<analysis>
Episode title: {analysis.get("episode_title", "")}
Hook: {analysis.get("hook", "")}
Themes: {", ".join(analysis.get("themes_json") or [])}
Chapters (milliseconds): {_format_chapters(analysis.get("chapters_json") or [])}
Key quotes: {_format_quotes(analysis.get("quotes_json") or [], limit=8)}
{guest_data}
</analysis>

<tone_instruction>{_tone_instruction(tone)}</tone_instruction>

Write show notes for this podcast episode in Markdown using this exact structure:

# {{episode_title}}
> {{one-sentence hook}}

## About the guest
{guest_section_note}

## Topics covered
- bullet list of themes and key topics discussed

## Timestamps
- [HH:MM:SS] Chapter title
(Convert chapter milliseconds to HH:MM:SS. If no chapters, skip this section.)

## Notable quotes
> "quote text" — Speaker Name

## Links mentioned
(List any URLs, books, tools, or resources explicitly mentioned in the transcript. If none found, skip this section.)

Return ONLY the Markdown. No preamble, no outer markdown fences around the whole output."""


def build_newsletter_prompt(analysis: dict, tone: str) -> str:
    guest_line = ""
    if analysis.get("guest_json"):
        guest_line = f"\nGuest: {json.dumps(analysis['guest_json'], ensure_ascii=False)}"

    return f"""<analysis>
Episode title: {analysis.get("episode_title", "")}
Hook: {analysis.get("hook", "")}
Themes: {", ".join(analysis.get("themes_json") or [])}
Key quotes: {_format_quotes(analysis.get("quotes_json") or [])}
{guest_line}
</analysis>

<tone_instruction>{_tone_instruction(tone)}</tone_instruction>

Write a newsletter issue about this podcast episode for Substack. Target: ~400 words.

Use this exact structure:

**Subject line:** (1 compelling line, under 60 characters)

(Hook paragraph — 2-3 sentences. Grab attention immediately. Reference the episode's core tension, surprise, or insight.)

## Takeaway 1: [Short title]
(2-3 sentences expanding on this key insight from the episode)

## Takeaway 2: [Short title]
(2-3 sentences expanding on this key insight from the episode)

## Takeaway 3: [Short title]
(2-3 sentences expanding on this key insight from the episode)

(Closing CTA paragraph — 2-3 sentences inviting readers to listen to the full episode)

Return ONLY the newsletter text in Markdown. No preamble, no outer markdown fences."""


def build_youtube_description_prompt(analysis: dict, tone: str) -> str:
    has_chapters = bool(analysis.get("chapters_json"))
    chapters_instruction = (
        "Include a timestamps section:\nMM:SS - Chapter Title\n"
        "(Convert chapter milliseconds to MM:SS format)"
        if has_chapters
        else "There are no chapters — skip the timestamps section."
    )

    guest_line = ""
    if analysis.get("guest_json"):
        guest_line = f"\nGuest: {json.dumps(analysis['guest_json'], ensure_ascii=False)}"

    return f"""<analysis>
Episode title: {analysis.get("episode_title", "")}
Hook: {analysis.get("hook", "")}
Themes / keywords: {", ".join(analysis.get("themes_json") or [])}
Chapters (milliseconds): {_format_chapters(analysis.get("chapters_json") or [])}
{guest_line}
</analysis>

<tone_instruction>{_tone_instruction(tone)}</tone_instruction>

Write a YouTube video description for this podcast episode with this structure:

1. SEO hook (first 150 characters — critical, shown in YouTube search preview without truncation)
2. 2-3 sentence episode summary paragraph
3. Timestamps section:
   {chapters_instruction}
4. Keywords section — 5-8 relevant search terms, one per line, prefixed with #
5. Social links placeholder on its own line: {{{{PODCAST_LINKS}}}}

Return ONLY the description text. No preamble, no markdown fences."""
