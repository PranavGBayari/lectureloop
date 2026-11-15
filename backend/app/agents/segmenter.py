"""Segmenter (Haiku): has the lecturer finished a concept?"""

from __future__ import annotations

from .. import llm, prompts
from ..models import SegmenterOutput, TranscriptChunk
from ._fmt import chunks_block, slides_block


async def run(
    open_chunks: list[TranscriptChunk],
    recent: list[TranscriptChunk],
    earlier_concepts: list[str],
    slides: list[str],
    open_name: str | None,
    force_close: bool = False,
) -> SegmenterOutput:
    parts = [
        f"<open_concept name=\"{open_name or 'unnamed'}\">\n{chunks_block(open_chunks)}\n</open_concept>",
        f"<recent>\n{chunks_block(recent)}\n</recent>",
        "<earlier_concepts>\n" + ("\n".join(earlier_concepts) or "(none)") + "\n</earlier_concepts>",
        f"<slides>\n{slides_block(slides)}\n</slides>",
    ]
    if force_close:
        parts.append("<force_close>The lecture has ended.</force_close>")
    return await llm.call(
        "segmenter",
        [{"role": "user", "content": "\n\n".join(parts)}],
        SegmenterOutput,
        system=prompts.load("segmenter"),
        tier="fast",
        max_tokens=1024,
        fallback=SegmenterOutput(boundary=False),
        step="force_close" if force_close else "segment",
    )
