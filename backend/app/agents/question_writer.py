"""Question writer (Sonnet): one grounded question for one student. Grounding is checked in code."""

from __future__ import annotations

import logging

from .. import llm, prompts
from ..guardrails.grounding import grounding_score, THRESHOLD
from ..models import Concept, Event, Mode, QuestionWriterOutput
from ._fmt import slides_block

log = logging.getLogger(__name__)


async def run(
    concept: Concept,
    span_text: str,
    slides: list[str],
    mode: Mode,
    mastery: float,
    difficulty: int,
    past_questions: list[str],
    purpose: str = "first question on this concept",
) -> QuestionWriterOutput | None:
    """Returns None if the model can't write a grounded question (no source, no question)."""
    user = "\n\n".join([
        f"<concept name=\"{concept.name}\">{concept.summary}</concept>",
        f"<transcript_span>\n{span_text}\n</transcript_span>",
        f"<slides>\n{slides_block(slides, 3000)}\n</slides>",
        f"<student mode=\"{mode}\" mastery=\"{mastery:.2f}\" />",
        f"<target difficulty=\"{difficulty}\" purpose=\"{purpose}\" />",
        "<already_asked>\n" + ("\n".join(f"- {q}" for q in past_questions) or "(none)") + "\n</already_asked>",
    ])
    messages = [{"role": "user", "content": user}]
    sources = [span_text, *slides]

    for attempt in (1, 2):
        try:
            out = await llm.call(
                "question_writer", messages, QuestionWriterOutput,
                system=prompts.load("question_writer"), tier="smart", max_tokens=1500,
                step=f"write#{attempt}",
            )
        except llm.InsufficientSource:
            return None
        score = grounding_score(out.source_quote, sources)
        grounded = score >= THRESHOLD
        await llm.log_event(Event(
            agent="question_writer", step=f"grounding#{attempt}", guardrail="grounding",
            outcome="pass" if grounded else f"fail ({score:.0f})",
        ))
        if grounded:
            return out
        # Regenerate once, telling the model what went wrong.
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": out.model_dump_json()},
            {"role": "user", "content": (
                f"Your source_quote \"{out.source_quote}\" does not appear in the transcript span or slides. "
                "Write the question again with a source_quote copied word for word from the material."
            )},
        ]
    log.info("question for %s skipped: ungrounded twice", concept.name)
    return None
