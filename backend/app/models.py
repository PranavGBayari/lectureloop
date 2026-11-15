"""The contract. Every collection, REST body, WS message and agent output lives here.

Frontend types are generated from the OpenAPI schema (see frontend/package.json `gen:types`),
so change shapes here first, then regenerate.
"""

from __future__ import annotations

import secrets
import string
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> datetime:
    return datetime.now(UTC)


def new_join_code() -> str:
    # 6 chars, no ambiguous characters (0/O, 1/I/L)
    alphabet = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1IL")
    return "".join(secrets.choice(alphabet) for _ in range(6))


# ---------------------------------------------------------------- enums

class SessionStatus(StrEnum):
    live = "live"
    ended = "ended"


class Mode(StrEnum):
    normal = "normal"
    support = "support"
    challenge = "challenge"


class QuestionKind(StrEnum):
    recall = "recall"
    explain = "explain"
    apply = "apply"
    spot_the_bug = "spot-the-bug"


class Verdict(StrEnum):
    correct = "correct"
    partial = "partial"
    wrong = "wrong"


class EscalationType(StrEnum):
    weak_concept = "weak_concept"
    low_confidence_grade = "low_confidence_grade"
    injection = "injection"
    impasse = "impasse"  # the same student wrong 3 times on one concept


class EscalationStatus(StrEnum):
    open = "open"
    approved = "approved"
    dismissed = "dismissed"
    overridden = "overridden"


class NextMove(StrEnum):
    ask_followup = "ask_followup"
    give_hint = "give_hint"
    trace_back = "trace_back"
    advance = "advance"
    retry = "retry"  # the answer was blocked by a guardrail; the same question stays open


class ErrorCode(StrEnum):
    invalid_input = "invalid_input"
    blocked_input = "blocked_input"
    llm_timeout = "llm_timeout"
    llm_bad_output = "llm_bad_output"
    not_found = "not_found"
    not_configured = "not_configured"  # an optional integration (e.g. ElevenLabs) has no API key
    upstream_error = "upstream_error"  # a third-party service failed or refused


# ---------------------------------------------------------------- collections

class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    join_code: str = Field(default_factory=new_join_code)
    status: SessionStatus = SessionStatus.live
    slides_text: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)


class TranscriptChunk(BaseModel):
    session_id: str
    seq: int
    t_start: float
    t_end: float
    text: str


class Concept(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    name: str
    summary: str
    t_start: float
    t_end: float | None = None
    source_chunk_ids: list[int] = Field(default_factory=list)
    prereq_ids: list[str] = Field(default_factory=list)


class Student(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    nickname: str
    mode: Mode = Mode.normal
    joined_at: datetime = Field(default_factory=now)


class Question(BaseModel):
    id: str = Field(default_factory=new_id)
    concept_id: str
    student_id: str
    difficulty: int = Field(ge=1, le=3)
    kind: QuestionKind
    text: str
    rubric: str
    source_quote: str
    source_t: float | None = None  # lecture time of the transcript chunk the quote is from; None = slides
    hints: list[str] = Field(min_length=3, max_length=3)
    created_at: datetime = Field(default_factory=now)


class Answer(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str | None = None
    question_id: str
    student_id: str
    text: str
    score: float = Field(ge=0, le=1)
    verdict: Verdict
    misconception: str | None = None
    grader_confidence: float = Field(ge=0, le=1)
    flags: list[str] = Field(default_factory=list)
    concept_id: str | None = None
    created_at: datetime = Field(default_factory=now)


class Mastery(BaseModel):
    student_id: str
    concept_id: str
    p_known: float = Field(default=0.3, ge=0, le=1)
    attempts: int = 0
    updated_at: datetime = Field(default_factory=now)


class Escalation(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    type: EscalationType
    payload: dict[str, Any] = Field(default_factory=dict)
    status: EscalationStatus = EscalationStatus.open
    resolved_by: str | None = None
    created_at: datetime = Field(default_factory=now)


class Event(BaseModel):
    """Observability log row. One per LLM call or guardrail decision."""

    ts: datetime = Field(default_factory=now)
    session_id: str | None = None
    agent: str
    step: str
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    guardrail: str | None = None
    outcome: str


# ---------------------------------------------------------------- REST bodies

class CreateSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CreateSessionResponse(BaseModel):
    id: str
    join_code: str


class ScribeTokenResponse(BaseModel):
    """A single-use ElevenLabs realtime Scribe token (15 minutes, consumed on connect). Never the API key."""

    token: str


class SlidesResponse(BaseModel):
    pages: int


class TranscriptChunkIn(BaseModel):
    t_start: float = Field(ge=0)
    t_end: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=4000)


class TranscriptRequest(BaseModel):
    chunks: list[TranscriptChunkIn] = Field(min_length=1, max_length=500)


class TranscriptResponse(BaseModel):
    accepted: int


class JoinRequest(BaseModel):
    join_code: str = Field(min_length=4, max_length=12)
    nickname: str = Field(min_length=1, max_length=40)


class JoinResponse(BaseModel):
    student_id: str
    session_id: str


class SessionPublic(BaseModel):
    id: str
    title: str
    join_code: str
    status: SessionStatus


class MisconceptionCount(BaseModel):
    text: str
    count: int


class HeatmapConcept(BaseModel):
    id: str
    name: str
    avg_mastery: float
    n_active: int
    top_misconception: str | None = None
    n_answers: int = 0
    n_struggling: int = 0  # answers graded partial or wrong
    misconceptions: list[MisconceptionCount] = Field(default_factory=list)  # top 3, most common first


class ModeCounts(BaseModel):
    normal: int = 0
    support: int = 0
    challenge: int = 0


class DashboardResponse(BaseModel):
    session: SessionPublic
    concepts: list[HeatmapConcept]
    n_students: int
    modes: ModeCounts = Field(default_factory=ModeCounts)
    room_score: float | None = None  # class challenge: mastery across the room, 0-1; None before any answers


class ResolveEscalationRequest(BaseModel):
    action: Literal["approve", "dismiss", "override", "edit"]
    edit: str | None = Field(default=None, max_length=2000)  # edited recap text (action "edit")
    verdict: Verdict | None = None  # the TA's grade (action "override")


class GuardrailStat(BaseModel):
    name: str
    checks: int
    triggered: int


class LatencyStat(BaseModel):
    agent: str
    calls: int
    p50_ms: int
    p95_ms: int
    tokens_in: int
    tokens_out: int


class RedteamCase(BaseModel):
    id: str
    kind: str
    expect: str
    got: str
    passed: bool


class RedteamReport(BaseModel):
    total: int
    passed: int
    rate: float
    cases: list[RedteamCase]


class OpsResponse(BaseModel):
    """Observability: everything is an aggregate over this session's event log."""

    guardrails: list[GuardrailStat]
    latency: list[LatencyStat]
    confidence_histogram: list[int]  # 10 buckets over grader confidence 0-1
    n_answers: int
    n_blocked: int
    llm_calls: int
    tokens_per_turn: float
    redteam: RedteamReport
    escalations: list[Escalation]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------- WebSocket

WSType = Literal[
    # server -> all
    "concept_opened",
    "concept_closed",
    # server -> student
    "question",
    "feedback",
    "feedback_delta",
    "hint",
    "trace_back",
    "recap",
    "status",
    "room_score",
    # student -> server
    "answer",
    "hint_request",
    "set_ready",
    # server -> lecturer
    "heatmap",
    "escalation",
    "replay_status",
    # lecturer -> server
    "push_recap",
    # either direction
    "error",
    "ping",
    "pong",
]


class WSMessage(BaseModel):
    type: WSType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class QuestionOut(BaseModel):
    """What a student sees. Never includes rubric, hints or source internals."""

    question_id: str
    text: str
    difficulty: int
    concept_id: str
    hints_available: int
    source_quote: str
    source_t: float | None = None  # seconds into the lecture where the quote was said; None = from slides


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    text: str


class SetReadyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready: bool


class StudentStatusOut(BaseModel):
    """Sent on connect and whenever mode/mastery/ready change, so a refresh restores state."""

    nickname: str
    mode: Mode
    ready: bool
    concepts: list[Concept]
    mastery: dict[str, float]  # concept_id -> p_known


class ReplayRequest(BaseModel):
    speed: float = Field(default=1.0, ge=0.5, le=8.0)


class ReplaySpeedRequest(BaseModel):
    speed: float = Field(ge=0.5, le=8.0)


class ReplayStatus(BaseModel):
    running: bool
    speed: float
    position_s: float
    duration_s: float


class HintRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str


class FeedbackOut(BaseModel):
    verdict: Verdict
    message: str
    mastery: float
    mode: Mode
    next: NextMove
    lecture_t: float = 0.0  # seconds into the lecture when this was graded
    blocked: bool = False  # stopped by the injection filter; not graded
    reviewed: bool = False  # a TA overrode the grade


class RecapOut(BaseModel):
    """A lecturer-approved recap, sent to every student."""

    concept_id: str
    concept_name: str
    text: str


class RoomScoreOut(BaseModel):
    """Class challenge: one score for the whole room, never a leaderboard."""

    score: float | None
    concepts: dict[str, float] = Field(default_factory=dict)  # concept_id -> the room's average mastery


class HintOut(BaseModel):
    level: int
    text: str


class TraceBackOut(BaseModel):
    from_concept: str
    to_concept: str
    message: str


class PushRecapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept_id: str
    text: str = Field(min_length=1, max_length=2000)


# ---------------------------------------------------------------- agent outputs

class InsufficientSource(BaseModel):
    insufficient_source: Literal[True]


class SegmenterConcept(BaseModel):
    name: str
    summary: str
    source_quote: str


class SegmenterOutput(BaseModel):
    boundary: bool
    new_topic_starts_at_seq: int | None = None
    concept: SegmenterConcept | None = None  # the concept that just finished
    next_topic: str | None = None
    prereqs: list[str] = Field(default_factory=list)


class QuestionWriterOutput(BaseModel):
    text: str
    kind: QuestionKind
    difficulty: int = Field(ge=1, le=3)
    rubric: str
    source_quote: str
    hints: list[str] = Field(min_length=3, max_length=3)


class GraderOutput(BaseModel):
    score: float = Field(ge=0, le=1)
    verdict: Verdict
    misconception: str | None = None
    related_prereq: str | None = None
    confidence: float = Field(ge=0, le=1)


class TutorOutput(BaseModel):
    message: str
    next: NextMove
    trace_to: str | None = None


class InjectionVerdict(BaseModel):
    injection: bool
    reason: str = ""


class LeakVerdict(BaseModel):
    leaks: bool


class InsightOutput(BaseModel):
    severity: Literal["low", "medium", "high"]
    summary: str
    recap_suggestion: str
