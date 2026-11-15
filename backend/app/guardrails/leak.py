"""Guardrail 5, answer-leak check: nothing the tutor says (messages or hints) may hand over the
rubric's key answer. Token overlap decides the clear cases; a Haiku yes/no check the borderline.
"""

from __future__ import annotations

import re

from .. import llm, prompts
from ..models import Event, LeakVerdict, Question

LEAK_AT = 0.5         # share of the rubric's distinctive words present -> leak
BORDERLINE_AT = 0.25  # between the two: ask the classifier
MIN_TERMS = 3

_STOP = set("""a an and are as at be been but by can could did do does for from had has have how if in into is it
its just may might more most must no not of on one only or other should so some such than that the their them then
there these they this those to too very was were what when where which who why will with would you your answer
answers correct student students explain explains explained mention mentions key idea ideas rubric
question because about also each every any all both using use used""".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z][a-z0-9*>-]{2,}", text.lower()) if t not in _STOP]


def distinctive_terms(rubric: str, question_text: str) -> set[str]:
    """Words the rubric uses that the question itself does not: the substance of the answer."""
    return set(_tokens(rubric)) - set(_tokens(question_text))


def overlap(message: str, rubric: str, question_text: str) -> float:
    terms = distinctive_terms(rubric, question_text)
    if len(terms) < MIN_TERMS:
        return 0.0
    return len(terms & set(_tokens(message))) / len(terms)


async def leaks(message: str, question: Question, *, what: str = "tutor_message") -> bool:
    score = overlap(message, question.rubric, question.text)
    if score >= LEAK_AT:
        leaked, stage = True, "overlap"
    elif score >= BORDERLINE_AT:
        user = (f"<question>{question.text}</question>\n\n<rubric>{question.rubric}</rubric>\n\n"
                f"<tutor_message>{message}</tutor_message>")
        # If the check itself fails, assume a leak: the safe canned message is always available.
        v = await llm.call("leak_checker", [{"role": "user", "content": user}], LeakVerdict,
                           system=prompts.load("leak"), tier="fast", max_tokens=100,
                           fallback=LeakVerdict(leaks=True), step="leak_check")
        leaked, stage = v.leaks, "classifier"
    else:
        leaked, stage = False, "overlap"
    await llm.log_event(Event(agent="guardrail", step=what, guardrail="answer_leak",
                              outcome=f"blocked:{stage} ({score:.2f})" if leaked else f"pass:{stage}"))
    return leaked


GENERIC_HINTS = [
    "Think back to how the lecturer introduced this idea.",
    "Re-read the quote from the lecture under the question. Which part does the question turn on?",
    "Try explaining it out loud in one sentence, then write that sentence down.",
]


def safe_hints(hints: list[str], rubric: str, question_text: str) -> list[str]:
    """Hints come from the question writer; replace any that give the answer away (overlap only)."""
    return [g if overlap(h, rubric, question_text) >= LEAK_AT else h for h, g in zip(hints, GENERIC_HINTS)]
