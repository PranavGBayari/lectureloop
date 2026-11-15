"""Grounding check: a question's source_quote must fuzzy-match the transcript span or slides."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

THRESHOLD = 80.0  # rapidfuzz scores 0-100; the plan's 0.8


def _norm(s: str) -> str:
    s = s.lower().replace("`", "")
    s = re.sub(r"[^\w\s*>.<-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def grounding_score(quote: str, sources: list[str]) -> float:
    q = _norm(quote)
    if len(q) < 8:
        return 0.0
    return max((fuzz.partial_ratio(q, _norm(src)) for src in sources if src), default=0.0)


def is_grounded(quote: str, sources: list[str]) -> bool:
    return grounding_score(quote, sources) >= THRESHOLD
