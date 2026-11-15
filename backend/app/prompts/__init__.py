"""System prompts. Each agent's prompt is its own .md plus the shared rules in _rules.md."""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


# Agents whose output is prose shown straight to a student. The shared "return
# {"insufficient_source": true}" rule is for JSON agents; told to a prose agent, it ends up on screen.
PLAIN_TEXT_AGENTS = {"tutor"}
_PLAIN_SOURCE_RULE = (
    "- Use only the provided transcript span and slides. If they do not cover something, do not invent it: "
    "keep to encouragement and the next step. Always reply in plain sentences, never JSON or code fences."
)


@lru_cache
def load(agent: str) -> str:
    rules = (_DIR / "_rules.md").read_text(encoding="utf-8")
    if agent in PLAIN_TEXT_AGENTS:
        lines = rules.splitlines()
        rules = "\n".join(_PLAIN_SOURCE_RULE if "insufficient_source" in ln else ln for ln in lines)
    return (_DIR / f"{agent}.md").read_text(encoding="utf-8").strip() + "\n\n" + rules
