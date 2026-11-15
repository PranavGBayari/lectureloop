"""Tutor (Sonnet, streamed): the message a student sees after an answer.

Code has already chosen the next move (see supervisor.choose_next); the tutor only writes the words.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from .. import llm, prompts
from ..models import GraderOutput, Mode, NextMove, Question
from ._fmt import slides_block, student_data

FALLBACK = {
    NextMove.advance: "Nice work, that's the key idea.",
    NextMove.ask_followup: "You're on the right track. Here's a follow-up to sharpen it.",
    NextMove.give_hint: "Not quite yet. Here's a hint to get you going.",
    NextMove.trace_back: "This builds on an earlier idea from the lecture. Let's check that first.",
}


async def stream(
    question: Question,
    answer_text: str,
    grade: GraderOutput,
    mode: Mode,
    next_move: NextMove,
    trace_to: str | None,
    span_text: str = "",
    slides: list[str] | None = None,
) -> AsyncIterator[str]:
    user = "\n\n".join([
        f"<transcript_span>\n{span_text or '(not available)'}\n</transcript_span>",
        f"<slides>\n{slides_block(slides or [], 2000)}\n</slides>",
        f"<question>{question.text}</question>",
        student_data(answer_text),
        f"<grade verdict=\"{grade.verdict}\" misconception=\"{grade.misconception or 'none'}\" />",
        f"<mode>{mode}</mode>",
        f"<next_move>{next_move}</next_move>" + (f"\n<trace_to>{trace_to}</trace_to>" if trace_to else ""),
    ])
    async for delta in llm.stream_text(
        "tutor", [{"role": "user", "content": user}],
        system=prompts.load("tutor"), tier="smart", max_tokens=1000, step="feedback",
    ):
        yield delta


_SENTENCE_END = ".!?\"”’)`"


def clean(message: str) -> str | None:
    """The tutor's words, made safe to show; None means "use the canned fallback instead".

    Rejects anything that is not prose (JSON such as {"insufficient_source": true}, code fences),
    and trims a message that was cut off mid-sentence back to its last complete sentence.
    """
    m = message.strip()
    if not m or "insufficient_source" in m or m.startswith(("{", "[", "```")):
        return None
    try:
        if isinstance(json.loads(m), (dict, list)):
            return None
    except ValueError:
        pass
    if m[-1] not in _SENTENCE_END:
        ends = [x.end() for x in re.finditer(r"[.!?][\"”’)`]*(?=\s|$)", m)]
        if not ends or ends[-1] < 20:
            return None
        m = m[:ends[-1]]
    return m
