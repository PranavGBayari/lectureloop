"""Red-team suite: runs every case in tests/redteam.jsonl through the real guardrail code.

Case kinds:
- injection: {"text"}                           expect "block" or "allow"
- leak:      {"question", "rubric", "message"}  expect "block" or "allow"

Used by pytest (the >= 90% gate) and by the ops page (pass rate). `uv run python -m app.redteam`
prints the table.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .guardrails import injection, leak
from .models import Question, QuestionKind, RedteamCase, RedteamReport

CASES_PATH = Path(__file__).resolve().parents[1] / "tests" / "redteam.jsonl"
_cached: RedteamReport | None = None


def load_cases() -> list[dict]:
    if not CASES_PATH.exists():
        return []
    return [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _run_case(c: dict) -> RedteamCase:
    if c["kind"] == "injection":
        blocked = (await injection.check(c["text"])).blocked
    else:
        q = Question(concept_id="redteam", student_id="redteam", difficulty=2, kind=QuestionKind.explain,
                     text=c["question"], rubric=c["rubric"], source_quote="-", hints=["-", "-", "-"])
        blocked = await leak.leaks(c["message"], q, what="redteam")
    got = "block" if blocked else "allow"
    return RedteamCase(id=c["id"], kind=c["kind"], expect=c["expect"], got=got, passed=got == c["expect"])


async def run() -> RedteamReport:
    cases = [await _run_case(c) for c in load_cases()]
    passed = sum(c.passed for c in cases)
    return RedteamReport(total=len(cases), passed=passed, rate=round(passed / len(cases), 3) if cases else 0.0,
                         cases=cases)


async def report() -> RedteamReport:
    """Cached per process: the suite is static, so one run is enough for the ops page."""
    global _cached
    if _cached is None:
        _cached = await run()
    return _cached


if __name__ == "__main__":
    r = asyncio.run(run())
    for c in r.cases:
        print(f"{'PASS' if c.passed else 'FAIL'}  {c.id:<12} {c.kind:<10} expect={c.expect:<6} got={c.got}")
    print(f"\n{r.passed}/{r.total} passed ({r.rate:.0%})")
