"""Grader (Haiku): score one answer against the rubric."""

from __future__ import annotations

from .. import llm, prompts
from ..models import GraderOutput, Question, Verdict
from ._fmt import student_data

# If grading fails, don't punish the student: neutral, zero-confidence (goes to TA review later).
FALLBACK = GraderOutput(score=0.5, verdict=Verdict.partial, misconception=None, confidence=0.0)


async def run(question: Question, answer_text: str, prereq_names: list[str]) -> GraderOutput:
    user = "\n\n".join([
        f"<question>{question.text}</question>",
        f"<rubric>{question.rubric}</rubric>",
        "<prerequisites>\n" + ("\n".join(prereq_names) or "(none)") + "\n</prerequisites>",
        student_data(answer_text),
    ])
    return await llm.call(
        "grader", [{"role": "user", "content": user}], GraderOutput,
        system=prompts.load("grader"), tier="fast", max_tokens=600, fallback=FALLBACK, step="grade",
    )
