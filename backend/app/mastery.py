"""Bayesian Knowledge Tracing update and the mode policy. Code decides the mode, never the model."""

from __future__ import annotations

from .models import Mode, Verdict

P_INIT = 0.3
SLIP = 0.1
GUESS = 0.2
LEARN = 0.15

SUPPORT_BELOW = 0.4  # after >= 2 attempts
CHALLENGE_ABOVE = 0.8
EXIT_SUPPORT_AT = 0.6  # hysteresis so the badge doesn't flicker
EXIT_CHALLENGE_BELOW = 0.6

# "A partial answer counts as half a correct answer."
VERDICT_WEIGHT = {Verdict.correct: 1.0, Verdict.partial: 0.5, Verdict.wrong: 0.0}


def bkt_update(p_known: float, verdict: Verdict) -> float:
    """One BKT step. The posterior is a weighted mix of the correct/wrong posteriors, then learning."""
    w = VERDICT_WEIGHT[verdict]
    p = p_known
    post_correct = p * (1 - SLIP) / (p * (1 - SLIP) + (1 - p) * GUESS)
    post_wrong = p * SLIP / (p * SLIP + (1 - p) * (1 - GUESS))
    post = w * post_correct + (1 - w) * post_wrong
    return min(1.0, max(0.0, post + (1 - post) * LEARN))


def next_mode(current: Mode, p_known: float, attempts: int) -> Mode:
    if p_known > CHALLENGE_ABOVE:
        return Mode.challenge
    if p_known < SUPPORT_BELOW and attempts >= 2:
        return Mode.support
    if current == Mode.support and p_known >= EXIT_SUPPORT_AT:
        return Mode.normal
    if current == Mode.challenge and p_known < EXIT_CHALLENGE_BELOW:
        return Mode.normal
    return current
