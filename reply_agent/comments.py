"""One contextual sentence and the owner's conditional greeting."""

import re
from typing import Final

from pydantic_core import PydanticCustomError

GREETING: Final = "자주 교류해요~"
MIN_BODY: Final = 8
MAX_BODY: Final = 120


def compose(body: str, *, unsubscribed: bool) -> str:
    """Accept a human/agent-written reaction; never invent a generic comment."""
    sentence = body.strip()
    if not MIN_BODY <= len(sentence) <= MAX_BODY or "\n" in sentence:
        msg = "comment_body"
        raise PydanticCustomError(msg, "Use one short 8-120 character sentence")
    if GREETING in sentence or any(
        token in sentence.lower() for token in ("http", "맞구독", "상호구독")
    ):
        msg = "comment_body"
        raise PydanticCustomError(msg, "Remove links and subscription requests")
    parts = [
        part
        for part in re.split(r"[.!?\u3002\uff01\uff1f]+(?:\s+|$)", sentence)
        if part.strip()
    ]
    if len(parts) != 1:
        msg = "comment_body"
        raise PydanticCustomError(msg, "Write a single reaction sentence")
    return f"{sentence} {GREETING}" if unsubscribed else sentence


def validate_comment(content: str, *, unsubscribed: bool) -> None:
    """Apply current policy on new reservations, not historical ledger parsing."""
    suffix = f" {GREETING}"
    body = content.removesuffix(suffix) if unsubscribed else content
    if content != compose(body, unsubscribed=unsubscribed):
        msg = "comment_greeting"
        raise PydanticCustomError(msg, "Greeting must match initial subscription state")
