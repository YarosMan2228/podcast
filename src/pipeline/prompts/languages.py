"""ISO-639-1 code → language name, for "write in the podcast's language" prompts.

Whisper reports the detected language as a two-letter code. Claude follows
"Write in Ukrainian" far more reliably than "Write in uk", so prompts go
through :func:`language_name`. Unknown codes fall back to the code itself,
which still works for most languages Claude knows.
"""
from __future__ import annotations

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
    "nl": "Dutch",
    "tr": "Turkish",
    "cs": "Czech",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "no": "Norwegian",
    "ro": "Romanian",
    "hu": "Hungarian",
    "el": "Greek",
    "he": "Hebrew",
    "ar": "Arabic",
    "hi": "Hindi",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "kk": "Kazakh",
    "ka": "Georgian",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "sr": "Serbian",
    "hr": "Croatian",
    "sk": "Slovak",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
}

DEFAULT_LANGUAGE = "en"


def language_name(code: str | None) -> str:
    """Human-readable name for *code*; ``None``/empty → English."""
    if not code:
        return _LANGUAGE_NAMES[DEFAULT_LANGUAGE]
    return _LANGUAGE_NAMES.get(code.lower().strip(), code)


def output_language_instruction(code: str | None) -> str:
    """One-paragraph instruction telling Claude which language to write in.

    Returns an empty string for English so existing English prompts stay
    byte-identical (and their prompt-cache prefixes stay warm).
    """
    if not code or code.lower().strip() == DEFAULT_LANGUAGE:
        return ""
    name = language_name(code)
    return (
        f"The podcast is in {name}. Write ALL generated text in {name} — "
        "titles, hooks, posts, hashtags, keywords, section headings included. "
        "Do not translate into English. Keep placeholders such as "
        "{{EPISODE_URL}} and {{PODCAST_LINKS}} exactly as they are."
    )
