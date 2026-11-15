"""Guardrail 1, input validation: length, control characters. (The 3-second rate limit needs
per-student state, so it lives in supervisor.handle_answer.)"""

from __future__ import annotations

import re

MAX_ANSWER_CHARS = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")


def clean_answer(text: str) -> str | None:
    """The cleaned answer, or None if it is empty or too long."""
    clean = _ZERO_WIDTH.sub("", _CONTROL.sub("", text)).strip()
    if not clean or len(clean) > MAX_ANSWER_CHARS:
        return None
    return clean
