"""Pulls the digits out of a transcript.

Whisper may write a spoken code as "4719", "4, 7, 1, 9" or "four seven one nine".
All of them become "4719". The prompt asks for single digits, so numbers said
as "forty-seven" aren't understood, and the check fails safely.
"""

from __future__ import annotations

import re

WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def spoken_digits(text: str) -> str:
    tokens = re.findall(r"[a-z]+|\d+", text.lower())
    return "".join(token if token.isdigit() else WORDS.get(token, "") for token in tokens)


def said_code(transcript: str, code: str) -> bool:
    """True if the code's digits appear, in order and together, in what was said."""
    return code in spoken_digits(transcript)
