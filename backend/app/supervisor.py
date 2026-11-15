"""Supervisor: a plain async loop (plan, act, observe). No LLM; it routes events to agents.

Flow for one concept:
1. A transcript chunk arrives -> per-session worker -> segmenter decides if a concept closed.
2. On a boundary the concept is saved and broadcast, and every ready student is queued a question.
3. A student answers -> grader -> BKT mastery + mode policy -> next move chosen in code.
4. The tutor streams the message; then the move is carried out (hint, follow-up, trace-back, advance).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from . import aggregate, db, llm
from .agents import grader, insight, question_writer, segmenter, tutor
from .agents._fmt import chunks_block, mmss
from .config import get_settings
from .guardrails import injection, leak, validate
from .guardrails.grounding import _norm as grounding_norm
from .mastery import P_INIT, bkt_update, next_mode
from .models import (
    Answer,
    Concept,
    DashboardResponse,
    ErrorCode,
    Escalation,
    EscalationStatus,
    EscalationType,
    Event,
    FeedbackOut,
    GraderOutput,
    HeatmapConcept,
    HintOut,
    Mastery,
    Mode,
    NextMove,
    Question,
    QuestionOut,
    QuestionWriterOutput,
    RecapOut,
    ReplayStatus,
    RoomScoreOut,
    Session,
    Student,
    StudentStatusOut,
    TranscriptChunk,
    Verdict,
    now,
)
from .ws import manager

log = logging.getLogger(__name__)

RECENT_WINDOW_S = 90.0
RATE_LIMIT_S = 3.0
MAX_PENDING = 2          # don't pile up more than 2 unanswered concepts on a student
MAX_FOLLOWUPS = 2        # per concept, so nobody gets stuck in a loop
FIRST_DIFFICULTY = {Mode.support: 1, Mode.normal: 2, Mode.challenge: 3}
HEATMAP_TICK_S = 2.0     # aggregates go to the lecturer at most this often
LOW_CONFIDENCE = 0.6     # grades below this go to the TA review queue
IMPASSE_WRONGS = 3       # same student wrong this many times on one concept
WEAK_MASTERY = 0.45      # a concept below this average...
WEAK_SHARE = 0.3         # ...with at least this share of the room answering is escalated


# ---------------------------------------------------------------- runtime state

@dataclass
class OpenConcept:
    concept: Concept
    chunks: list[TranscriptChunk] = field(default_factory=list)


@dataclass
class SessionRuntime:
    session_id: str
    chunks: list[TranscriptChunk] = field(default_factory=list)
    open: OpenConcept | None = None
    closed: list[Concept] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker: asyncio.Task | None = None
    replay: asyncio.Task | None = None
    replay_speed: float = 1.0
    replay_paused: bool = False
    replay_pos: float = 0.0
    replay_duration: float = 0.0
    ended: bool = False
    # (concept_id, mode, difficulty) -> task producing the shared first question for that bucket
    first_q: dict[tuple[str, Mode, int], asyncio.Task] = field(default_factory=dict)
    ticker: asyncio.Task | None = None
    dirty: bool = False                      # aggregates changed since the last heatmap broadcast
    escalated: set[str] = field(default_factory=set)  # concept ids already escalated as weak
    last_room: dict | None = None


@dataclass
class StudentRuntime:
    student_id: str
    session_id: str
    ready: bool = True
    current: Question | None = None
    pending: deque[str] = field(default_factory=deque)  # concept ids
    hints_used: dict[str, int] = field(default_factory=dict)
    followups: dict[str, int] = field(default_factory=dict)  # concept_id -> count
    traced: set[str] = field(default_factory=set)  # concept ids already traced back from
    asked: dict[str, list[str]] = field(default_factory=dict)  # concept_id -> question texts
    last_answer_at: float = 0.0
    wrongs: dict[str, int] = field(default_factory=dict)  # concept_id -> wrong answers (impasse trigger)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    busy: bool = False  # generating a question


_sessions: dict[str, SessionRuntime] = {}
_students: dict[str, StudentRuntime] = {}


def session_rt(session_id: str) -> SessionRuntime:
    rt = _sessions.get(session_id)
    if rt is None:
        rt = _sessions[session_id] = SessionRuntime(session_id)
    if rt.worker is None or rt.worker.done():
        rt.worker = asyncio.create_task(_session_worker(rt))
    if rt.ticker is None or rt.ticker.done():
        rt.ticker = asyncio.create_task(_ticker(rt))
    return rt


def student_rt(student: Student) -> StudentRuntime:
    rt = _students.get(student.id)
    if rt is None:
        rt = _students[student.id] = StudentRuntime(student.id, student.session_id)
    return rt


def reset() -> None:
    """Test hook."""
    for rt in _sessions.values():
        for t in (rt.worker, rt.replay, rt.ticker):
            if t and not t.done():
                t.cancel()
    _sessions.clear()
    _students.clear()


# ---------------------------------------------------------------- store helpers

async def _session_doc(session_id: str) -> dict:
    return await db.get_store().find_one("sessions", {"id": session_id}) or {}


async def _student(student_id: str) -> Student | None:
    d = await db.get_store().find_one("students", {"id": student_id})
    return Student.model_validate(d) if d else None


async def _concept(concept_id: str) -> Concept | None:
    d = await db.get_store().find_one("concepts", {"id": concept_id})
    return Concept.model_validate(d) if d else None


async def _mastery(student_id: str, concept_id: str) -> Mastery:
    d = await db.get_store().find_one("mastery", {"student_id": student_id, "concept_id": concept_id})
    return Mastery.model_validate(d) if d else Mastery(student_id=student_id, concept_id=concept_id)


async def _span_text(concept: Concept) -> str:
    docs = await db.get_store().find("transcript_chunks", {"session_id": concept.session_id})
    chunks = [TranscriptChunk.model_validate(d) for d in docs if d["seq"] in set(concept.source_chunk_ids)]
    return chunks_block(sorted(chunks, key=lambda c: c.seq))


async def _timeline(session_id: str) -> list[Concept]:
    docs = await db.get_store().find("concepts", {"session_id": session_id})
    return sorted((Concept.model_validate(d) for d in docs), key=lambda c: c.t_start)


# ---------------------------------------------------------------- transcript -> concepts

async def add_chunks(session_id: str, chunks: list[tuple[float, float, str]]) -> int:
    rt = session_rt(session_id)
    if rt.ended:
        raise ValueError("this lecture has ended")
    for t_start, t_end, text in chunks:
        seq = len(rt.chunks)
        c = TranscriptChunk(session_id=session_id, seq=seq, t_start=t_start, t_end=t_end, text=text)
        rt.chunks.append(c)
        await db.get_store().insert("transcript_chunks", c.model_dump(mode="json"))
        await rt.queue.put(c)
    return len(chunks)


async def end_lecture(session_id: str) -> None:
    rt = session_rt(session_id)
    await rt.queue.put("END")


async def _session_worker(rt: SessionRuntime) -> None:
    """Processes chunks one at a time so segmenter calls never overlap for a session."""
    llm.current_session.set(rt.session_id)
    while True:
        item = await rt.queue.get()
        try:
            if item == "END":
                await _close_open(rt, force=True)
                rt.ended = True
                await db.get_store().update_one("sessions", {"id": rt.session_id}, {"status": "ended"})
            else:
                await _on_chunk(rt, item)
        except Exception:  # noqa: BLE001 - one bad chunk must not kill the lecture
            log.exception("session worker error")


async def _open_concept(rt: SessionRuntime, name: str | None, chunks: list[TranscriptChunk]) -> None:
    c = Concept(session_id=rt.session_id, name=name or "Listening…", summary="",
                t_start=chunks[0].t_start, source_chunk_ids=[x.seq for x in chunks])
    rt.open = OpenConcept(concept=c, chunks=list(chunks))
    await db.get_store().insert("concepts", c.model_dump(mode="json"))
    await manager.to_session(rt.session_id, "concept_opened", {"concept": c.model_dump(mode="json")})


async def _on_chunk(rt: SessionRuntime, chunk: TranscriptChunk) -> None:
    if rt.open is None:
        await _open_concept(rt, None, [chunk])
        return
    rt.open.chunks.append(chunk)
    rt.open.concept.source_chunk_ids.append(chunk.seq)
    if chunk.t_end - rt.open.concept.t_start < get_settings().min_concept_s:
        return  # plan: minimum 45 s per concept
    await _close_open(rt, force=False)


async def _close_open(rt: SessionRuntime, force: bool) -> None:
    if rt.open is None:
        return
    oc = rt.open
    s = await _session_doc(rt.session_id)
    latest = oc.chunks[-1].t_end
    recent = [c for c in rt.chunks if c.t_end >= latest - RECENT_WINDOW_S]
    out = await segmenter.run(
        oc.chunks, recent, [c.name for c in rt.closed], s.get("slides_text", []),
        None if oc.concept.name == "Listening…" else oc.concept.name, force_close=force,
    )
    if not out.boundary and not force:
        return

    seqs = [c.seq for c in oc.chunks]
    start = out.new_topic_starts_at_seq
    if force:
        finished, rest = oc.chunks, []
    elif start is not None and seqs[0] < start <= seqs[-1]:
        finished = [c for c in oc.chunks if c.seq < start]
        rest = [c for c in oc.chunks if c.seq >= start]
    else:
        finished, rest = oc.chunks[:-1], oc.chunks[-1:]
    if not finished:
        return

    concept = oc.concept
    concept.name = (out.concept.name if out.concept else None) or (
        concept.name if concept.name != "Listening…" else f"Concept {len(rt.closed) + 1}")
    concept.summary = out.concept.summary if out.concept else ""
    concept.t_end = finished[-1].t_end
    concept.source_chunk_ids = [c.seq for c in finished]
    concept.prereq_ids = _match_prereqs(out.prereqs, rt.closed)
    await db.get_store().update_one("concepts", {"id": concept.id}, concept.model_dump(mode="json"))
    rt.closed.append(concept)
    rt.open = None
    await manager.to_session(rt.session_id, "concept_closed", {"concept": concept.model_dump(mode="json")})
    log.info("concept closed: %s (%s-%ss) prereqs=%s", concept.name, concept.t_start, concept.t_end,
             concept.prereq_ids)

    if rest:
        await _open_concept(rt, out.next_topic, rest)
    asyncio.create_task(_dispatch(rt, concept))


def _match_prereqs(names: list[str], closed: list[Concept]) -> list[str]:
    if not names or not closed:
        return []
    by_name = {c.name: c.id for c in closed}
    ids = []
    for n in names:
        m = process.extractOne(n, list(by_name), scorer=fuzz.WRatio, score_cutoff=85)
        if m and by_name[m[0]] not in ids:
            ids.append(by_name[m[0]])
    return ids


# ---------------------------------------------------------------- questions

async def _dispatch(rt: SessionRuntime, concept: Concept) -> None:
    students = [Student.model_validate(d) for d in await db.get_store().find("students", {"session_id": rt.session_id})]
    await asyncio.gather(*(_enqueue(s, concept.id) for s in students), return_exceptions=True)


async def _enqueue(student: Student, concept_id: str) -> None:
    srt = student_rt(student)
    srt.pending.append(concept_id)
    while len(srt.pending) > MAX_PENDING:
        srt.pending.popleft()
    if srt.current is None and srt.ready and not srt.busy:
        await deliver_next(student.id)


async def deliver_next(student_id: str) -> None:
    student = await _student(student_id)
    if not student:
        return
    srt = student_rt(student)
    while srt.pending and srt.current is None and srt.ready:
        concept = await _concept(srt.pending.popleft())
        if not concept:
            continue
        srt.busy = True
        try:
            q = await _first_question(student, concept)
        finally:
            srt.busy = False
        if q:
            await _send_question(srt, q)
            return


async def _first_question(student: Student, concept: Concept) -> Question | None:
    """First question on a concept, shared across students with the same mode and difficulty."""
    rt = session_rt(concept.session_id)
    difficulty = FIRST_DIFFICULTY[student.mode]
    key = (concept.id, student.mode, difficulty)
    task = rt.first_q.get(key)
    if task is None:
        task = rt.first_q[key] = asyncio.create_task(
            _generate(concept, student.mode, P_INIT, difficulty, [], "first question on this concept"))
    try:
        out = await asyncio.shield(task)
    except llm.LLMError as e:
        rt.first_q.pop(key, None)
        await _error(student.id, e.code, "Couldn't write a question for that concept right now.")
        return None
    return await _save_question(out, concept, student.id) if out else None


async def _generate(concept: Concept, mode: Mode, mastery: float, difficulty: int,
                    past: list[str], purpose: str) -> QuestionWriterOutput | None:
    s = await _session_doc(concept.session_id)
    return await question_writer.run(concept, await _span_text(concept), s.get("slides_text", []),
                                     mode, mastery, difficulty, past, purpose)


def _lecture_t(session_id: str) -> float:
    """How far into the lecture we are: the end of the newest transcript chunk."""
    chunks = session_rt(session_id).chunks
    return chunks[-1].t_end if chunks else 0.0


async def _quote_time(concept: Concept, quote: str) -> float | None:
    """When the quoted words were said: t_start of the best-matching chunk. None if it is from the slides."""
    docs = await db.get_store().find("transcript_chunks", {"session_id": concept.session_id})
    wanted = set(concept.source_chunk_ids)
    q = grounding_norm(quote)
    best_t, best = None, 60.0  # below this the quote is not really from any one chunk
    for d in sorted((d for d in docs if d["seq"] in wanted), key=lambda d: d["seq"]):
        score = fuzz.partial_ratio(q, grounding_norm(d["text"]))
        if score > best:  # strict: ties go to the earliest chunk
            best_t, best = float(d["t_start"]), score
    return best_t


async def _save_question(out: QuestionWriterOutput, concept: Concept, student_id: str) -> Question:
    q = Question(concept_id=concept.id, student_id=student_id, difficulty=out.difficulty, kind=out.kind,
                 text=out.text, rubric=out.rubric, source_quote=out.source_quote,
                 source_t=await _quote_time(concept, out.source_quote),
                 hints=leak.safe_hints(out.hints, out.rubric, out.text))  # guardrail 5 covers hints too
    await db.get_store().insert("questions", q.model_dump(mode="json"))
    return q


async def _send_question(srt: StudentRuntime, q: Question, resend: bool = False) -> None:
    srt.current = q
    if not resend:
        srt.asked.setdefault(q.concept_id, []).append(q.text)
    out = QuestionOut(question_id=q.id, text=q.text, difficulty=q.difficulty, concept_id=q.concept_id,
                      hints_available=len(q.hints) - srt.hints_used.get(q.id, 0),
                      source_quote=q.source_quote, source_t=q.source_t)
    await manager.to_student(srt.student_id, "question", out.model_dump())


# ---------------------------------------------------------------- answers

def choose_next(grade: GraderOutput, q: Question, srt: StudentRuntime, mode: Mode,
                prereq: Concept | None) -> NextMove:
    """The next move is policy, decided in code from the grade and the student's state."""
    followups = srt.followups.get(q.concept_id, 0)
    hints_left = len(q.hints) - srt.hints_used.get(q.id, 0)
    if grade.verdict == Verdict.correct:
        if mode == Mode.challenge and q.difficulty < 3 and followups < MAX_FOLLOWUPS:
            return NextMove.ask_followup  # harder question
        return NextMove.advance
    if grade.verdict == Verdict.partial:
        return NextMove.ask_followup if followups < MAX_FOLLOWUPS else NextMove.advance
    # wrong
    if prereq is not None and q.concept_id not in srt.traced:
        return NextMove.trace_back
    if hints_left > 0:
        return NextMove.give_hint
    return NextMove.ask_followup if followups < MAX_FOLLOWUPS else NextMove.advance


async def _error(student_id: str, code: ErrorCode, message: str) -> None:
    await manager.to_student(student_id, "error", {"code": code, "message": message})


async def _stream_out(student_id: str, question_id: str, message: str) -> None:
    for word in re.findall(r"\S+\s*", message):
        await manager.to_student(student_id, "feedback_delta", {"question_id": question_id, "delta": word})
        await asyncio.sleep(0.02)


async def _blocked(student: Student, q: Question, text: str, inj: injection.InjectionResult) -> None:
    """An injection attempt: not graded, mastery untouched, a polite reply, and a log entry."""
    await db.get_store().insert("answers", Answer(
        session_id=student.session_id, question_id=q.id, student_id=student.id, text=text, score=0,
        verdict=Verdict.wrong, grader_confidence=1.0, concept_id=q.concept_id, flags=["injection"],
    ).model_dump(mode="json"))
    m = await _mastery(student.id, q.concept_id)
    await _stream_out(student.id, q.id, injection.POLITE_REPLY)
    fb = FeedbackOut(verdict=Verdict.wrong, message=injection.POLITE_REPLY, mastery=round(m.p_known, 3),
                     mode=student.mode, next=NextMove.retry, blocked=True,
                     lecture_t=_lecture_t(student.session_id))
    await manager.to_student(student.id, "feedback", {**fb.model_dump(), "question_id": q.id})
    concept = await _concept(q.concept_id)
    await _open_escalation(Escalation(
        session_id=student.session_id, type=EscalationType.injection,
        payload={"text": text[:200], "stage": inj.stage, "matched": inj.reason,
                 "concept_name": concept.name if concept else None},
    ))


async def handle_answer(student_id: str, question_id: str, text: str) -> None:
    student = await _student(student_id)
    if not student:
        return
    llm.current_session.set(student.session_id)
    srt = student_rt(student)
    async with srt.lock:
        q = srt.current
        if q is None or q.id != question_id:
            await _error(student_id, ErrorCode.not_found, "That question is no longer open.")
            return
        # Guardrail 1: input validation and the rate limit.
        clean = validate.clean_answer(text)
        if clean is None:
            await llm.log_event(Event(agent="guardrail", step="validate", guardrail="input_validation",
                                      outcome="rejected:length"))
            await _error(student_id, ErrorCode.invalid_input, "Answers must be 1-500 characters.")
            return
        if time.monotonic() - srt.last_answer_at < RATE_LIMIT_S:
            await llm.log_event(Event(agent="guardrail", step="validate", guardrail="input_validation",
                                      outcome="rejected:rate_limit"))
            await _error(student_id, ErrorCode.invalid_input, "One answer every 3 seconds, please.")
            return
        await llm.log_event(Event(agent="guardrail", step="validate", guardrail="input_validation", outcome="pass"))
        srt.last_answer_at = time.monotonic()

        # Guardrail 2: injection filter. A blocked answer never reaches the grader.
        inj = await injection.check(clean)
        if inj.blocked:
            await _blocked(student, q, clean, inj)
            return

        concept = await _concept(q.concept_id)
        prereqs = [c for c in [await _concept(i) for i in (concept.prereq_ids if concept else [])] if c]
        grade = await grader.run(q, clean, [p.name for p in prereqs])

        # Mastery + mode policy (code decides the mode).
        m = await _mastery(student.id, q.concept_id)
        m.p_known = bkt_update(m.p_known, grade.verdict)
        m.attempts += 1
        m.updated_at = now()
        await db.get_store().update_one("mastery", {"student_id": student.id, "concept_id": q.concept_id},
                                        m.model_dump(mode="json"), upsert=True)
        mode = next_mode(student.mode, m.p_known, m.attempts)
        if mode != student.mode:
            await db.get_store().update_one("students", {"id": student.id}, {"mode": mode})
            student.mode = mode
        answer = Answer(
            session_id=student.session_id, question_id=q.id, student_id=student.id, text=clean,
            score=grade.score, verdict=grade.verdict, misconception=grade.misconception,
            grader_confidence=grade.confidence, concept_id=q.concept_id,
            flags=["low_confidence"] if grade.confidence < LOW_CONFIDENCE else [],
        )
        await db.get_store().insert("answers", answer.model_dump(mode="json"))
        mark_dirty(student.session_id)
        await _escalate_answer(student, srt, q, concept, answer)

        prereq = _pick_prereq(grade, prereqs)
        move = choose_next(grade, q, srt, mode, prereq)

        # Tutor message. It is buffered so guardrail 5 (answer leak) can check it before a single
        # word reaches the student, then sent word by word.
        message = ""
        try:
            sdoc = await _session_doc(student.session_id)
            async for delta in tutor.stream(q, clean, grade, mode, move, prereq.name if prereq else None,
                                            span_text=await _span_text(concept) if concept else "",
                                            slides=sdoc.get("slides_text", [])):
                message += delta
        except llm.LLMError:
            message = ""
        # Never raw model output: JSON (e.g. insufficient_source) or a cut-off sentence becomes the fallback.
        message = tutor.clean(message) or tutor.FALLBACK[move]
        if grade.verdict != Verdict.correct and await leak.leaks(message, q):
            message = tutor.FALLBACK[move]  # replaced by a plain nudge; the hint ladder does the teaching
        await _stream_out(student.id, q.id, message)
        fb = FeedbackOut(verdict=grade.verdict, message=message,
                         mastery=round(m.p_known, 3), mode=mode, next=move, lecture_t=_lecture_t(student.session_id))
        await manager.to_student(student.id, "feedback", {**fb.model_dump(), "question_id": q.id})
        await send_status(student.id)

        await _act(student, srt, q, concept, grade, move, prereq)


def _pick_prereq(grade: GraderOutput, prereqs: list[Concept]) -> Concept | None:
    if grade.verdict != Verdict.wrong or not prereqs:
        return None
    if grade.related_prereq:
        m = process.extractOne(grade.related_prereq, [p.name for p in prereqs], scorer=fuzz.WRatio, score_cutoff=80)
        if m:
            return prereqs[m[2]]
    return None


async def _act(student: Student, srt: StudentRuntime, q: Question, concept: Concept | None,
               grade: GraderOutput, move: NextMove, prereq: Concept | None) -> None:
    if move == NextMove.give_hint:
        await _give_hint(srt, q)
        return
    srt.current = None
    if move == NextMove.advance or concept is None:
        await deliver_next(student.id)
        return

    m = await _mastery(student.id, (prereq or concept).id)
    if move == NextMove.trace_back and prereq is not None:
        srt.traced.add(concept.id)
        srt.pending.appendleft(concept.id)  # come back to this concept afterwards
        await manager.to_student(student.id, "trace_back", {
            "from_concept": concept.name, "to_concept": prereq.name, "to_concept_id": prereq.id,
            "at": mmss(prereq.t_start),
            "message": f"This builds on {prereq.name} from {mmss(prereq.t_start)}. Quick check?",
        })
        target, difficulty, purpose = prereq, 1, (
            f"quick check on a prerequisite; the student's mistake on '{concept.name}' was: "
            f"{grade.misconception or 'unclear'}")
    else:  # ask_followup
        srt.followups[concept.id] = srt.followups.get(concept.id, 0) + 1
        harder = grade.verdict == Verdict.correct
        difficulty = min(3, q.difficulty + 1) if harder else max(1, q.difficulty - 1)
        target = concept
        purpose = ("harder follow-up: the student answered correctly" if harder else
                   f"simpler follow-up; the student's answer was {grade.verdict}"
                   + (f", misconception: {grade.misconception}" if grade.misconception else ""))

    srt.busy = True
    try:
        out = await _generate(target, student.mode, m.p_known, difficulty,
                              srt.asked.get(target.id, []), purpose)
    except llm.LLMError as e:
        out = None
        await _error(student.id, e.code, "Couldn't write a follow-up right now.")
    finally:
        srt.busy = False
    if out:
        await _send_question(srt, await _save_question(out, target, student.id))
    else:
        await deliver_next(student.id)


async def _give_hint(srt: StudentRuntime, q: Question) -> None:
    level = srt.hints_used.get(q.id, 0)
    if level >= len(q.hints):
        await _error(srt.student_id, ErrorCode.invalid_input, "No hints left for this question.")
        return
    srt.hints_used[q.id] = level + 1
    await manager.to_student(srt.student_id, "hint", HintOut(level=level + 1, text=q.hints[level]).model_dump())


async def handle_hint(student_id: str, question_id: str) -> None:
    student = await _student(student_id)
    if not student:
        return
    llm.current_session.set(student.session_id)
    srt = student_rt(student)
    if srt.current is None or srt.current.id != question_id:
        await _error(student_id, ErrorCode.not_found, "That question is no longer open.")
        return
    await _give_hint(srt, srt.current)


async def set_ready(student_id: str, ready: bool) -> None:
    student = await _student(student_id)
    if not student:
        return
    llm.current_session.set(student.session_id)
    srt = student_rt(student)
    srt.ready = ready
    await send_status(student_id)
    if ready and srt.current is None and not srt.busy:
        await deliver_next(student_id)


# ---------------------------------------------------------------- connect / status

async def send_status(student_id: str) -> None:
    student = await _student(student_id)
    if not student:
        return
    srt = student_rt(student)
    concepts = await _timeline(student.session_id)
    docs = await db.get_store().find("mastery", {"student_id": student_id})
    out = StudentStatusOut(nickname=student.nickname, mode=student.mode, ready=srt.ready,
                           concepts=concepts, mastery={d["concept_id"]: d["p_known"] for d in docs})
    await manager.to_student(student_id, "status", out.model_dump(mode="json"))


async def on_student_connect(student: Student) -> None:
    llm.current_session.set(student.session_id)
    srt = student_rt(student)
    await send_status(student.id)
    rt = session_rt(student.session_id)
    if rt.last_room:
        await manager.to_student(student.id, "room_score", rt.last_room)
    if srt.current is not None:
        await _send_question(srt, srt.current, resend=True)  # after a refresh
    elif srt.ready and not srt.busy:
        await deliver_next(student.id)


# ---------------------------------------------------------------- lecturer side: tick, escalations, recaps

def mark_dirty(session_id: str) -> None:
    session_rt(session_id).dirty = True


async def _session(session_id: str) -> Session | None:
    d = await _session_doc(session_id)
    return Session.model_validate(d) if d else None


async def broadcast_heatmap(session_id: str) -> DashboardResponse | None:
    s = await _session(session_id)
    if not s:
        return None
    d = await aggregate.dashboard(s)
    await manager.to_lecturers(session_id, "heatmap", {
        "concepts": [c.model_dump() for c in d.concepts],
        "n_students": d.n_students,
        "n_online": len(manager.online_students(session_id)),
        "modes": d.modes.model_dump(),
        "room_score": d.room_score,
    })
    # Class challenge: students see one number for the whole room, never each other.
    room = RoomScoreOut(score=d.room_score,
                        concepts={c.id: c.avg_mastery for c in d.concepts if c.n_active > 0}).model_dump()
    rt = session_rt(session_id)
    if room != rt.last_room:
        rt.last_room = room
        await asyncio.gather(*(manager.to_student(sid, "room_score", room)
                               for sid in manager.online_students(session_id)))
    return d


async def _ticker(rt: SessionRuntime) -> None:
    """Every 2 seconds, if anything changed: aggregates to the lecturer, then escalation triggers."""
    llm.current_session.set(rt.session_id)
    while True:
        await asyncio.sleep(HEATMAP_TICK_S)
        if not rt.dirty:
            continue
        rt.dirty = False
        try:
            d = await broadcast_heatmap(rt.session_id)
            if d:
                await _check_weak(rt, d)
        except Exception:  # noqa: BLE001 - the tick must survive a bad aggregate
            log.exception("heatmap tick failed")


async def _check_weak(rt: SessionRuntime, d: DashboardResponse) -> None:
    closed = {c.id for c in rt.closed}
    need = max(1, math.ceil(WEAK_SHARE * d.n_students))
    for c in d.concepts:
        if (c.id in closed and c.id not in rt.escalated and c.n_active >= need and c.n_answers >= 2
                and c.avg_mastery < WEAK_MASTERY):
            rt.escalated.add(c.id)
            asyncio.create_task(_escalate_weak(rt, c, d.n_students))


async def _escalate_weak(rt: SessionRuntime, heat: HeatmapConcept, n_students: int) -> None:
    concept = await _concept(heat.id)
    if not concept:
        return
    out = await insight.run(concept, await _span_text(concept), heat, n_students)
    await _open_escalation(Escalation(
        session_id=rt.session_id, type=EscalationType.weak_concept,
        payload={"concept_id": concept.id, "concept_name": concept.name, "avg_mastery": heat.avg_mastery,
                 "n_active": heat.n_active, "n_students": n_students, "severity": out.severity,
                 "summary": out.summary, "recap_suggestion": out.recap_suggestion},
    ))


async def _open_escalation(esc: Escalation) -> None:
    await db.get_store().insert("escalations", esc.model_dump(mode="json"))
    await manager.to_lecturers(esc.session_id, "escalation", {"escalation": esc.model_dump(mode="json")})


async def _escalate_answer(student: Student, srt: StudentRuntime, q: Question, concept: Concept | None,
                           answer: Answer) -> None:
    """Guardrail 7 triggers that hang off a single graded answer: low confidence and impasse."""
    base = {"student_id": student.id, "question_id": q.id, "answer_id": answer.id, "concept_id": q.concept_id,
            "concept_name": concept.name if concept else None, "question": q.text, "rubric": q.rubric,
            "answer": answer.text, "verdict": answer.verdict, "confidence": answer.grader_confidence}
    if answer.grader_confidence < LOW_CONFIDENCE:
        await _open_escalation(Escalation(session_id=student.session_id,
                                          type=EscalationType.low_confidence_grade, payload=base))
    if answer.verdict == Verdict.wrong:
        srt.wrongs[q.concept_id] = srt.wrongs.get(q.concept_id, 0) + 1
        if srt.wrongs[q.concept_id] == IMPASSE_WRONGS:
            await _open_escalation(Escalation(session_id=student.session_id, type=EscalationType.impasse,
                                              payload={**base, "wrong_count": IMPASSE_WRONGS}))


async def push_recap(session_id: str, concept_id: str, text: str) -> int:
    """Send a lecturer-approved recap to every student in the room. Returns how many were online."""
    concept = await _concept(concept_id)
    if not concept or concept.session_id != session_id:
        raise ValueError("unknown concept for this session")
    out = RecapOut(concept_id=concept.id, concept_name=concept.name, text=text.strip()).model_dump()
    online = manager.online_students(session_id)
    await asyncio.gather(*(manager.to_student(sid, "recap", out) for sid in online))
    return len(online)


async def resolve_escalation(esc: Escalation, action: str, edit: str | None, verdict: Verdict | None) -> Escalation:
    """Human in the loop. Raises ValueError for an action that does not fit the escalation type."""
    if esc.status != EscalationStatus.open:
        raise ValueError("this escalation is already resolved")
    llm.current_session.set(esc.session_id)
    p = esc.payload
    if action == "dismiss":
        esc.status = EscalationStatus.dismissed
    elif esc.type == EscalationType.weak_concept and action in ("approve", "edit"):
        text = (edit or "").strip() if action == "edit" else p.get("recap_suggestion", "")
        if not text:
            raise ValueError("edit: recap text is required")
        p["sent_text"] = text
        p["sent_to"] = await push_recap(esc.session_id, p["concept_id"], text)
        esc.status = EscalationStatus.approved
    elif esc.type == EscalationType.low_confidence_grade and action == "override":
        if verdict is None:
            raise ValueError("verdict: required to override a grade")
        await _override_grade(p, verdict)
        p["ta_verdict"] = verdict
        esc.status = EscalationStatus.overridden
    elif esc.type == EscalationType.low_confidence_grade and action == "approve":
        esc.status = EscalationStatus.approved  # the TA agrees with the grader
    elif esc.type == EscalationType.impasse and action == "approve":
        await _unstick(p)
        esc.status = EscalationStatus.approved
    elif esc.type == EscalationType.injection and action == "approve":
        esc.status = EscalationStatus.approved  # acknowledged
    else:
        raise ValueError(f"action '{action}' does not apply to a {esc.type} escalation")
    esc.resolved_by = "lecturer" if esc.type == EscalationType.weak_concept else "ta"
    await db.get_store().update_one("escalations", {"id": esc.id}, esc.model_dump(mode="json"))
    await manager.to_lecturers(esc.session_id, "escalation", {"escalation": esc.model_dump(mode="json")})
    return esc


async def _override_grade(p: dict, verdict: Verdict) -> None:
    """A TA overrides one grade: fix the answer, replay BKT for that concept, tell the student, resume."""
    store = db.get_store()
    student = await _student(p["student_id"])
    if not student:
        return
    score = {Verdict.correct: 1.0, Verdict.partial: 0.5, Verdict.wrong: 0.0}[verdict]
    await store.update_one("answers", {"id": p["answer_id"]},
                           {"verdict": verdict, "score": score, "misconception": None,
                            "flags": ["low_confidence", "ta_override"]})
    # Mastery is replayed from the answer history rather than patched, so later answers stay consistent.
    history = sorted((a for a in await store.find("answers", {"student_id": student.id, "concept_id": p["concept_id"]})
                      if "injection" not in a.get("flags", [])), key=lambda a: a["created_at"])
    p_known = P_INIT
    for a in history:
        p_known = bkt_update(p_known, Verdict(a["verdict"]))
    await store.update_one("mastery", {"student_id": student.id, "concept_id": p["concept_id"]},
                           {"p_known": p_known, "attempts": len(history), "updated_at": now().isoformat()}, upsert=True)
    mode = next_mode(student.mode, p_known, len(history))
    if mode != student.mode:
        await store.update_one("students", {"id": student.id}, {"mode": mode})
    fb = FeedbackOut(verdict=verdict, message=f"A teaching assistant reviewed your answer and marked it {verdict}.",
                     mastery=round(p_known, 3), mode=mode, next=NextMove.advance, reviewed=True,
                     lecture_t=_lecture_t(student.session_id))
    await manager.to_student(student.id, "feedback", {**fb.model_dump(), "question_id": p["question_id"]})
    await send_status(student.id)
    mark_dirty(student.session_id)
    srt = student_rt(student)
    if verdict == Verdict.correct and srt.current is not None and srt.current.id == p["question_id"]:
        srt.current = None  # they were still stuck on it: the session resumes
        await deliver_next(student.id)


async def _unstick(p: dict) -> None:
    """Impasse resolved by a TA: give the student the idea in one line and move them on."""
    student = await _student(p["student_id"])
    concept = await _concept(p["concept_id"])
    if not student or not concept:
        return
    text = (f"You've had a few goes at {concept.name.lower()}, so here is the idea in one line: "
            f"{concept.summary} We'll move on, and you can come back to it after the lecture.")
    await manager.to_student(student.id, "recap", RecapOut(concept_id=concept.id, concept_name=concept.name,
                                                           text=text).model_dump())
    srt = student_rt(student)
    if srt.current is not None and srt.current.concept_id == concept.id:
        srt.current = None
        await deliver_next(student.id)


# ---------------------------------------------------------------- demo replay

def load_demo() -> dict:
    p = Path(get_settings().demo_transcript)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[1] / p).resolve()
    return json.loads(p.read_text(encoding="utf-8"))


def replay_status(session_id: str) -> ReplayStatus:
    rt = session_rt(session_id)
    return ReplayStatus(running=bool(rt.replay and not rt.replay.done() and not rt.replay_paused),
                        speed=rt.replay_speed,
                        position_s=round(rt.replay_pos, 1), duration_s=rt.replay_duration)


async def start_replay(session_id: str, speed: float) -> ReplayStatus:
    rt = session_rt(session_id)
    if rt.replay and not rt.replay.done():
        rt.replay_speed, rt.replay_paused = speed, False  # resume
        return replay_status(session_id)
    if rt.chunks or rt.ended:
        raise ValueError("This session already has a transcript. Create a new session to replay again.")
    demo = load_demo()
    s = await _session_doc(session_id)
    if not s.get("slides_text") and demo.get("slides_text"):
        await db.get_store().update_one("sessions", {"id": session_id}, {"slides_text": demo["slides_text"]})
    chunks = demo["chunks"]
    rt.replay_speed = speed
    rt.replay_pos = 0.0
    rt.replay_duration = float(chunks[-1]["t_end"]) if chunks else 0.0
    rt.replay = asyncio.create_task(_replay_loop(rt, chunks))
    return replay_status(session_id)


def set_replay_speed(session_id: str, speed: float) -> ReplayStatus:
    session_rt(session_id).replay_speed = speed
    return replay_status(session_id)


def pause_replay(session_id: str) -> ReplayStatus:
    session_rt(session_id).replay_paused = True
    return replay_status(session_id)


def cancel_replay(session_id: str) -> None:
    rt = session_rt(session_id)
    if rt.replay and not rt.replay.done():
        rt.replay.cancel()


async def _replay_loop(rt: SessionRuntime, chunks: list[dict]) -> None:
    tick, i, last_status = 0.25, 0, 0.0
    try:
        while i < len(chunks):
            await asyncio.sleep(tick)
            if rt.replay_paused:
                continue
            rt.replay_pos += tick * rt.replay_speed
            due = []
            while i < len(chunks) and chunks[i]["t_end"] <= rt.replay_pos:
                due.append((float(chunks[i]["t_start"]), float(chunks[i]["t_end"]), chunks[i]["text"]))
                i += 1
            if due:
                await add_chunks(rt.session_id, due)
            if rt.replay_pos - last_status >= 1.0 or due:
                last_status = rt.replay_pos
                await manager.to_lecturers(rt.session_id, "replay_status",
                                           replay_status(rt.session_id).model_dump())
        await end_lecture(rt.session_id)
    finally:
        await manager.to_lecturers(rt.session_id, "replay_status", replay_status(rt.session_id).model_dump()
                                   | {"running": False})
