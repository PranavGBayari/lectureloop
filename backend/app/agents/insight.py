"""Insight (Sonnet): turns one weak concept's aggregates into an alert and a suggested recap."""

from __future__ import annotations

from .. import llm, prompts
from ..models import Concept, HeatmapConcept, InsightOutput


def fallback(concept: Concept, heat: HeatmapConcept) -> InsightOutput:
    top = heat.misconceptions[0].text if heat.misconceptions else None
    return InsightOutput(
        severity="high" if heat.avg_mastery < 0.3 else "medium",
        summary=(f"{heat.n_struggling} of {heat.n_answers} answers on {concept.name} needed support"
                 + (f"; the most common mix-up: {top}." if top else ".")),
        recap_suggestion=(f"Quick recap of {concept.name.lower()}: {concept.summary} "
                          "Take a moment to put that in your own words.").strip(),
    )


async def run(concept: Concept, span_text: str, heat: HeatmapConcept, n_students: int) -> InsightOutput:
    misc = "\n".join(f"- {m.text} ({m.count} answers)" for m in heat.misconceptions) or "(none recorded)"
    user = "\n\n".join([
        f"<concept name=\"{concept.name}\">{concept.summary}</concept>",
        f"<transcript_span>\n{span_text}\n</transcript_span>",
        (f"<aggregates students=\"{n_students}\" answering=\"{heat.n_active}\" answers=\"{heat.n_answers}\" "
         f"needing_support=\"{heat.n_struggling}\" avg_mastery=\"{heat.avg_mastery:.2f}\" />"),
        f"<misconceptions>\n{misc}\n</misconceptions>",
    ])
    try:
        return await llm.call("insight", [{"role": "user", "content": user}], InsightOutput,
                              system=prompts.load("insight"), tier="smart", max_tokens=700,
                              fallback=fallback(concept, heat), step="insight")
    except (llm.LLMError, llm.InsufficientSource):
        return fallback(concept, heat)  # the lecturer still gets an alert if the model is down
