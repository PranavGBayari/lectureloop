"""Guardrail 2, injection filter: regex for the common patterns, then a Haiku classifier for
anything borderline. A blocked answer is never graded.

Two regex tiers keep honest answers flowing: BLOCK patterns are unambiguous attempts to steer the
model; SUSPECT words ("ignore", "grade", "instructions"...) also turn up in real answers ("we can
ignore the Nothing case"), so those go to the classifier instead of being blocked outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import llm, prompts
from ..agents._fmt import student_data
from ..models import Event, InjectionVerdict

_I = re.IGNORECASE
BLOCK = [re.compile(p, _I) for p in (
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instructions?|prompts?|rules|directions|guidelines|rubric)\b",
    r"\bforget (everything|all (of )?(that|this)|what you were told)\b",
    r"\b(system|developer|hidden|initial) (prompt|message|instructions?)\b",
    r"\b(give|award|grant|assign)\b.{0,20}\b(full|top|maximum|max|perfect|100 ?%?)\b.{0,12}\b(marks?|scores?|credit|points|grade)\b",
    r"\bmark\b.{0,25}\b(as )?(correct|right)\b",
    r"\b(grade|score)\b.{0,25}\b(as )?(correct|full|perfect|100)\b",
    r"\bset (my |the )?(score|grade|mastery|verdict)\b",
    r"\b(you are|you're) now\b",
    r"\b(act|behave|respond) as (if|though|an?|the)\b",
    r"\bpretend (to be|you|that)\b",
    r"</?\s*(system|assistant|user|human|student_answer|rubric|instructions?|grade|next_move)\b",
    r"\[/?\s*(system|inst)\s*\]|<<\s*sys\s*>>|^#{2,}\s*(system|instructions?)\b",
    r"\b(reveal|show|print|repeat|output|tell me|give me|what is|what's)\b.{0,30}\b(rubric|answer key|model answer|system prompt|your instructions|the hints)\b",
    r"\b(jailbreak|dan mode|developer mode|do anything now)\b",
    r"\bnew (instructions?|rules?|task)\s*:",
    r"\bend of (student )?(answer|input)\b",
)]
SUSPECT = re.compile(
    r"\b(ignore|instructions?|prompt|grade|grader|score|marks?|rubric|correct answer|as an ai|assistant|"
    r"the model|claude|chatgpt|language model|you must|you should|obey|comply)\b", _I)
# Spacing/punctuation tricks ("i.g.n.o.r.e previous"): match on the letters only.
SQUASHED = re.compile(
    r"ignore(all|any|your|the|previous|prior|above)+(instructions|prompt|rules)|systemprompt|fullmarks|"
    r"markthis(as)?correct|disregard(all|your|previous)+instructions")

POLITE_REPLY = ("That reads like an instruction rather than an answer, so I can't grade it. "
                "Have another go at the question in your own words.")


@dataclass
class InjectionResult:
    blocked: bool
    stage: str  # "regex" | "classifier" | "none"
    reason: str = ""


def regex_stage(text: str) -> InjectionResult | None:
    """A decision if the regex tier can make one, else None (borderline: ask the classifier)."""
    for p in BLOCK:
        m = p.search(text)
        if m:
            return InjectionResult(True, "regex", m.group(0)[:60])
    squashed = re.sub(r"[^a-z]", "", text.lower())
    m = SQUASHED.search(squashed)
    if m:
        return InjectionResult(True, "regex", m.group(0)[:60])
    if SUSPECT.search(text):
        return None
    return InjectionResult(False, "none")


async def check(text: str) -> InjectionResult:
    result = regex_stage(text)
    if result is None:
        # If the classifier itself fails we let the answer through: it still reaches the grader
        # only as data inside <student_answer> tags, and the grader scores instructions as wrong.
        v = await llm.call(
            "injection_classifier", [{"role": "user", "content": student_data(text)}], InjectionVerdict,
            system=prompts.load("injection"), tier="fast", max_tokens=200,
            fallback=InjectionVerdict(injection=False, reason="classifier unavailable"), step="classify",
        )
        result = InjectionResult(v.injection, "classifier", v.reason[:60])
    await llm.log_event(Event(
        agent="guardrail", step="injection", guardrail="injection",
        outcome=f"blocked:{result.stage}" if result.blocked else f"pass:{result.stage}",
    ))
    return result
