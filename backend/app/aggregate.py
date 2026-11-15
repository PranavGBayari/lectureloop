"""Aggregates for the lecturer. Nothing here returns an individual student's work: the dashboard is
counts and averages per concept; the ops view is counts over the event log plus the review queue."""

from __future__ import annotations

from collections import Counter, defaultdict

from . import db, redteam
from .models import (
    Concept,
    DashboardResponse,
    Escalation,
    GuardrailStat,
    HeatmapConcept,
    LatencyStat,
    MisconceptionCount,
    ModeCounts,
    OpsResponse,
    Session,
    SessionPublic,
)

GUARDRAILS = ("input_validation", "injection", "schema_validation", "grounding", "answer_leak")


def _graded(answers: list[dict]) -> list[dict]:
    """Answers that were actually graded (injection attempts are blocked before the grader)."""
    return [a for a in answers if "injection" not in a.get("flags", [])]


async def dashboard(s: Session) -> DashboardResponse:
    store = db.get_store()
    concepts = sorted((Concept.model_validate(c) for c in await store.find("concepts", {"session_id": s.id})),
                      key=lambda c: c.t_start)
    students = await store.find("students", {"session_id": s.id})
    modes = ModeCounts(**Counter(st.get("mode", "normal") for st in students))
    heat = []
    for c in concepts:
        rows = [m for m in await store.find("mastery", {"concept_id": c.id}) if m.get("attempts", 0) > 0]
        answers = _graded(await store.find("answers", {"concept_id": c.id}))
        misc = Counter(a["misconception"] for a in answers if a.get("misconception"))
        top3 = [MisconceptionCount(text=t, count=n) for t, n in misc.most_common(3)]
        avg = sum(m["p_known"] for m in rows) / len(rows) if rows else 0.0
        heat.append(HeatmapConcept(id=c.id, name=c.name, avg_mastery=round(avg, 3), n_active=len(rows),
                                   top_misconception=top3[0].text if top3 else None,
                                   n_answers=len(answers),
                                   n_struggling=sum(1 for a in answers if a.get("verdict") != "correct"),
                                   misconceptions=top3))
    active = [h for h in heat if h.n_active > 0]
    weight = sum(h.n_active for h in active)
    return DashboardResponse(
        session=SessionPublic(id=s.id, title=s.title, join_code=s.join_code, status=s.status),
        concepts=heat,
        n_students=len(students),
        modes=modes,
        room_score=round(sum(h.avg_mastery * h.n_active for h in active) / weight, 3) if weight else None,
    )


def _pct(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    return sorted_vals[min(len(sorted_vals) - 1, round(q * (len(sorted_vals) - 1)))]


def _triggered(outcome: str) -> bool:
    return not (outcome in ("ok", "pass") or outcome.startswith("pass"))


async def ops(session_id: str) -> OpsResponse:
    store = db.get_store()
    events = await store.find("events", {"session_id": session_id})
    answers = await store.find("answers", {"session_id": session_id})
    graded = _graded(answers)

    checks: dict[str, list[int]] = {g: [0, 0] for g in GUARDRAILS}
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        g = e.get("guardrail")
        if g:
            row = checks.setdefault(g, [0, 0])
            row[0] += 1
            row[1] += _triggered(e.get("outcome", ""))
        if e.get("latency_ms", 0) > 0:
            by_agent[e["agent"]].append(e)

    latency = []
    for agent, rows in sorted(by_agent.items()):
        ms = sorted(r["latency_ms"] for r in rows)
        latency.append(LatencyStat(agent=agent, calls=len(rows), p50_ms=_pct(ms, 0.5), p95_ms=_pct(ms, 0.95),
                                   tokens_in=sum(r.get("tokens_in", 0) for r in rows),
                                   tokens_out=sum(r.get("tokens_out", 0) for r in rows)))
    hist = [0] * 10
    for a in graded:
        hist[min(9, int(a.get("grader_confidence", 0) * 10))] += 1
    tokens = sum(s.tokens_in + s.tokens_out for s in latency)
    escalations = sorted((Escalation.model_validate(d) for d in await store.find("escalations", {"session_id": session_id})),
                         key=lambda e: e.created_at, reverse=True)
    return OpsResponse(
        guardrails=[GuardrailStat(name=g, checks=c, triggered=t) for g, (c, t) in checks.items()],
        latency=latency,
        confidence_histogram=hist,
        n_answers=len(graded),
        n_blocked=len(answers) - len(graded),
        llm_calls=sum(s.calls for s in latency),
        tokens_per_turn=round(tokens / len(answers), 1) if answers else 0.0,
        redteam=await redteam.report(),
        escalations=escalations,
    )
