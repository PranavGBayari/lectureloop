"""Small helpers for building agent inputs."""

from __future__ import annotations

from ..models import TranscriptChunk


def chunks_block(chunks: list[TranscriptChunk]) -> str:
    return "\n".join(f"[seq {c.seq} | {c.t_start:.0f}-{c.t_end:.0f}s] {c.text}" for c in chunks)


def slides_block(slides: list[str], limit: int = 6000) -> str:
    text = "\n".join(f"[slide {i + 1}] {s}" for i, s in enumerate(slides))
    return text[:limit] if text else "(no slides)"


def student_data(text: str) -> str:
    """Wrap a student's answer as data. Angle brackets are escaped so it can't close the tag."""
    safe = text.replace("<", "&lt;").replace(">", "&gt;")
    return f"<student_answer>\n{safe}\n</student_answer>"


def mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"
