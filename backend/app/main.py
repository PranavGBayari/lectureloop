"""FastAPI app: REST routes + WebSocket endpoints. The supervisor does the routing to agents."""

from __future__ import annotations

import asyncio
import os
import io
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import aggregate, db, scribe, supervisor
from .config import get_settings
from .llm import LLMError
from .models import (
    AnswerIn,
    Concept,
    CreateSessionRequest,
    CreateSessionResponse,
    DashboardResponse,
    ErrorCode,
    ErrorResponse,
    Escalation,
    HintRequestIn,
    JoinRequest,
    JoinResponse,
    OpsResponse,
    PushRecapIn,
    ReplayRequest,
    ReplaySpeedRequest,
    ReplayStatus,
    ResolveEscalationRequest,
    ScribeTokenResponse,
    Session,
    SessionPublic,
    SessionStatus,
    SetReadyIn,
    SlidesResponse,
    Student,
    TranscriptRequest,
    TranscriptResponse,
    WSMessage,
)
from .ws import manager

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lectureloop")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_store()
    if os.environ.get("VERCEL") and getattr(db.get_store(), "name", "") == "memory":
        # Every function instance would have its own private store: sessions, students and answers would
        # vanish between requests. A real deployment needs MongoDB.
        log.error("Running on Vercel with the in-memory store: set MONGODB_URI, or data will not persist "
                  "between function instances.")
    yield
    supervisor.reset()


class StripApiPrefix:
    """Accept every route both as /path and as /api/path (HTTP and WebSocket).

    On Vercel the backend is public at /api/* and the request reaches it with the prefix still on;
    locally and on Render it is served at the root. Stripping a leading "/api" here means the same
    routes work in both places. Only a whole leading segment is stripped: /api/health and /api
    match, /apixyz does not. Pure ASGI (not BaseHTTPMiddleware) so WebSocket scopes pass through.
    """

    PREFIX = "/api"

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            path: str = scope["path"]
            if path == self.PREFIX or path.startswith(self.PREFIX + "/"):
                scope = dict(scope)  # never mutate the server's scope in place
                scope["path"] = path[len(self.PREFIX):] or "/"
                raw = scope.get("raw_path")
                if raw:  # keep the raw (still percent-encoded) path in step with the decoded one
                    prefix = self.PREFIX.encode()
                    if raw == prefix or raw.startswith(prefix + b"/") or raw.startswith(prefix + b"?"):
                        scope["raw_path"] = raw[len(prefix):] or b"/"
        await self.app(scope, receive, send)


app = FastAPI(title="LectureLoop API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last, so it is outermost: the prefix is gone before CORS or routing look at the path.
app.add_middleware(StripApiPrefix)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


# ---------------------------------------------------------------- errors: always {error:{code,message}}

class APIError(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def _err(status: int, code: ErrorCode, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(APIError)
async def _api_error(_: Request, e: APIError):
    return _err(e.status, e.code, e.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError):
    first = e.errors()[0] if e.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return _err(422, ErrorCode.invalid_input, f"{where}: {first.get('msg', 'invalid input')}")


@app.exception_handler(LLMError)
async def _llm_error(_: Request, e: LLMError):
    return _err(504 if e.code == ErrorCode.llm_timeout else 502, e.code, e.message)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, e: StarletteHTTPException):
    code = ErrorCode.not_found if e.status_code == 404 else ErrorCode.invalid_input
    return _err(e.status_code, code, str(e.detail))


# ---------------------------------------------------------------- helpers

async def _get_session(session_id: str) -> Session:
    doc = await db.get_store().find_one("sessions", {"id": session_id})
    if not doc:
        raise APIError(404, ErrorCode.not_found, f"session {session_id} not found")
    return Session.model_validate(doc)


async def _get_student(student_id: str) -> Student | None:
    doc = await db.get_store().find_one("students", {"id": student_id})
    return Student.model_validate(doc) if doc else None


# ---------------------------------------------------------------- REST

@app.get("/health")
async def health() -> dict[str, Any]:
    s = get_settings()
    return {"status": "ok", "storage": getattr(db.get_store(), "name", "unknown"), "llm": s.llm_provider,
            "elevenlabs": scribe.configured(),  # whether the lecturer can use ElevenLabs live transcription
            "storage_note": db.storage_note}  # why in-memory storage is in use, or null (MongoDB is connected)


def _require_shared_store() -> None:
    """On Vercel an in-memory store is private to one function instance: a session created on one
    instance is "not found" on the next. Refuse with the real reason instead of failing mysteriously."""
    if os.environ.get("VERCEL") and getattr(db.get_store(), "name", "") == "memory":
        raise APIError(503, ErrorCode.not_configured,
                       "This deployment has no shared database, so sessions would vanish between requests "
                       f"({db.storage_note or 'MongoDB is not connected'}). Set MONGODB_URI to a MongoDB Atlas "
                       "connection string in the Vercel project settings, then redeploy.")


@app.post("/sessions", response_model=CreateSessionResponse, responses=ERROR_RESPONSES)
async def create_session(body: CreateSessionRequest) -> CreateSessionResponse:
    _require_shared_store()
    store = db.get_store()
    session = Session(title=body.title.strip())
    while await store.find_one("sessions", {"join_code": session.join_code}):
        session = Session(title=session.title)  # regenerate on the (rare) code clash
    await store.insert("sessions", session.model_dump(mode="json"))
    log.info("session created %s code=%s", session.id, session.join_code)
    return CreateSessionResponse(id=session.id, join_code=session.join_code)


@app.get("/sessions/{session_id}", response_model=SessionPublic, responses=ERROR_RESPONSES)
async def get_session(session_id: str) -> SessionPublic:
    s = await _get_session(session_id)
    return SessionPublic(id=s.id, title=s.title, join_code=s.join_code, status=s.status)


@app.post("/sessions/{session_id}/slides", response_model=SlidesResponse, responses=ERROR_RESPONSES)
async def upload_slides(session_id: str, file: UploadFile = File(...)) -> SlidesResponse:
    await _get_session(session_id)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise APIError(422, ErrorCode.invalid_input, "file: slides must be under 20 MB")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [re.sub(r"\s+", " ", p.extract_text() or "").strip() for p in reader.pages]
    except Exception as e:  # noqa: BLE001 - any parse failure is the client's file
        raise APIError(422, ErrorCode.invalid_input, f"file: not a readable PDF ({type(e).__name__})") from e
    await db.get_store().update_one("sessions", {"id": session_id}, {"slides_text": pages})
    return SlidesResponse(pages=len(pages))


@app.get("/sessions/{session_id}/scribe-token", response_model=ScribeTokenResponse, responses=ERROR_RESPONSES)
async def scribe_token(session_id: str, response: Response) -> ScribeTokenResponse:
    """A single-use ElevenLabs Scribe token for the lecturer's microphone. The API key stays on the server."""
    s = await _get_session(session_id)
    if s.status == SessionStatus.ended:
        raise APIError(409, ErrorCode.invalid_input, "This lecture has ended.")
    try:
        token = await scribe.create_token()
    except scribe.ScribeError as e:
        raise APIError(e.status, e.code, e.message) from e
    response.headers["Cache-Control"] = "no-store"  # a token is consumed on use: never cache one
    return ScribeTokenResponse(token=token)


@app.post("/sessions/{session_id}/transcript", response_model=TranscriptResponse, responses=ERROR_RESPONSES)
async def add_transcript(session_id: str, body: TranscriptRequest) -> TranscriptResponse:
    await _get_session(session_id)
    try:
        n = await supervisor.add_chunks(session_id, [(c.t_start, c.t_end, c.text) for c in body.chunks])
    except ValueError as e:
        raise APIError(409, ErrorCode.invalid_input, str(e)) from e
    return TranscriptResponse(accepted=n)


@app.post("/sessions/{session_id}/replay", response_model=ReplayStatus, responses=ERROR_RESPONSES)
async def start_replay(session_id: str, body: ReplayRequest) -> ReplayStatus:
    await _get_session(session_id)
    try:
        return await supervisor.start_replay(session_id, body.speed)
    except ValueError as e:
        raise APIError(409, ErrorCode.invalid_input, str(e)) from e


@app.post("/sessions/{session_id}/replay/speed", response_model=ReplayStatus, responses=ERROR_RESPONSES)
async def replay_speed(session_id: str, body: ReplaySpeedRequest) -> ReplayStatus:
    await _get_session(session_id)
    return supervisor.set_replay_speed(session_id, body.speed)


@app.post("/sessions/{session_id}/replay/pause", response_model=ReplayStatus, responses=ERROR_RESPONSES)
async def replay_pause(session_id: str) -> ReplayStatus:
    """Pause; POST /replay again to resume."""
    await _get_session(session_id)
    return supervisor.pause_replay(session_id)


@app.get("/sessions/{session_id}/replay", response_model=ReplayStatus, responses=ERROR_RESPONSES)
async def replay_get(session_id: str) -> ReplayStatus:
    await _get_session(session_id)
    return supervisor.replay_status(session_id)


@app.post("/sessions/{session_id}/end", response_model=SessionPublic, responses=ERROR_RESPONSES)
async def end_session(session_id: str) -> SessionPublic:
    s = await _get_session(session_id)
    supervisor.cancel_replay(session_id)
    await supervisor.end_lecture(session_id)
    s.status = SessionStatus.ended
    return SessionPublic(id=s.id, title=s.title, join_code=s.join_code, status=s.status)


@app.post("/join", response_model=JoinResponse, responses=ERROR_RESPONSES)
async def join(body: JoinRequest) -> JoinResponse:
    _require_shared_store()
    store = db.get_store()
    code = body.join_code.strip().upper()
    doc = await store.find_one("sessions", {"join_code": code})
    if not doc:
        raise APIError(404, ErrorCode.not_found, "No lecture with that join code")
    nickname = re.sub(r"[\x00-\x1f\x7f]", "", body.nickname).strip()
    if not nickname:
        raise APIError(422, ErrorCode.invalid_input, "nickname: must not be empty")
    student = Student(session_id=doc["id"], nickname=nickname)
    await store.insert("students", student.model_dump(mode="json"))
    supervisor.mark_dirty(doc["id"])
    return JoinResponse(student_id=student.id, session_id=doc["id"])


@app.get("/sessions/{session_id}/dashboard", response_model=DashboardResponse, responses=ERROR_RESPONSES)
async def dashboard(session_id: str) -> DashboardResponse:
    return await aggregate.dashboard(await _get_session(session_id))


@app.get("/sessions/{session_id}/ops", response_model=OpsResponse, responses=ERROR_RESPONSES)
async def ops(session_id: str) -> OpsResponse:
    """Observability: guardrail counts, latency, tokens, red-team pass rate and the review queue."""
    await _get_session(session_id)
    return await aggregate.ops(session_id)


@app.get("/sessions/{session_id}/escalations", response_model=list[Escalation], responses=ERROR_RESPONSES)
async def list_escalations(session_id: str) -> list[Escalation]:
    await _get_session(session_id)
    docs = await db.get_store().find("escalations", {"session_id": session_id})
    return sorted((Escalation.model_validate(d) for d in docs), key=lambda e: e.created_at, reverse=True)


@app.post("/escalations/{escalation_id}/resolve", response_model=Escalation, responses=ERROR_RESPONSES)
async def resolve_escalation(escalation_id: str, body: ResolveEscalationRequest) -> Escalation:
    doc = await db.get_store().find_one("escalations", {"id": escalation_id})
    if not doc:
        raise APIError(404, ErrorCode.not_found, f"escalation {escalation_id} not found")
    esc = Escalation.model_validate(doc)
    try:
        return await supervisor.resolve_escalation(esc, body.action, body.edit, body.verdict)
    except ValueError as e:
        status = 409 if "already resolved" in str(e) else 422
        raise APIError(status, ErrorCode.invalid_input, str(e)) from e


# ---------------------------------------------------------------- WebSockets

def _spawn(coro) -> None:
    """Run a handler without blocking the socket's receive loop; log failures."""
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: not t.cancelled() and t.exception() and log.error("handler failed", exc_info=t.exception()))


@app.websocket("/ws/student/{student_id}")
async def ws_student(ws: WebSocket, student_id: str) -> None:
    student = await _get_student(student_id)
    await ws.accept()
    if not student:
        await ws.send_text(WSMessage(type="error", payload={"code": ErrorCode.not_found,
                                                            "message": "Unknown student"}).model_dump_json())
        await ws.close(code=4404)
        return
    manager.add_student(student.session_id, student.id, ws)
    supervisor.mark_dirty(student.session_id)
    try:
        await supervisor.on_student_connect(student)
        while True:
            raw = await ws.receive_text()
            try:
                msg = WSMessage.model_validate_json(raw)
                if msg.type == "answer":
                    a = AnswerIn.model_validate(msg.payload)
                    _spawn(supervisor.handle_answer(student.id, a.question_id, a.text))
                elif msg.type == "hint_request":
                    h = HintRequestIn.model_validate(msg.payload)
                    _spawn(supervisor.handle_hint(student.id, h.question_id))
                elif msg.type == "set_ready":
                    r = SetReadyIn.model_validate(msg.payload)
                    _spawn(supervisor.set_ready(student.id, r.ready))
                elif msg.type == "ping":
                    await ws.send_text(WSMessage(type="pong").model_dump_json())
                else:
                    raise ValueError(f"unsupported message type '{msg.type}'")
            except (ValidationError, ValueError) as e:
                await ws.send_text(WSMessage(type="error", payload={"code": ErrorCode.invalid_input,
                                                                    "message": str(e)[:300]}).model_dump_json())
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_student(student.id, ws)
        supervisor.mark_dirty(student.session_id)


@app.websocket("/ws/lecturer/{session_id}")
async def ws_lecturer(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    try:
        await _get_session(session_id)
    except APIError:
        await ws.send_text(WSMessage(type="error", payload={"code": ErrorCode.not_found,
                                                            "message": "Unknown session"}).model_dump_json())
        await ws.close(code=4404)
        return
    manager.add_lecturer(session_id, ws)
    supervisor.session_rt(session_id)
    try:
        await supervisor.broadcast_heatmap(session_id)  # a fresh screen gets data now, not at the next tick
        for e in await list_escalations(session_id):
            await ws.send_text(WSMessage(type="escalation",
                                         payload={"escalation": e.model_dump(mode="json")}).model_dump_json())
        await ws.send_text(WSMessage(type="replay_status",
                                     payload=supervisor.replay_status(session_id).model_dump()).model_dump_json())
        for c in await supervisor._timeline(session_id):
            kind = "concept_closed" if c.t_end is not None else "concept_opened"
            await ws.send_text(WSMessage(type=kind, payload={"concept": c.model_dump(mode="json")}).model_dump_json())
        while True:
            raw = await ws.receive_text()
            try:
                msg = WSMessage.model_validate_json(raw)
                if msg.type == "ping":
                    await ws.send_text(WSMessage(type="pong").model_dump_json())
                elif msg.type == "push_recap":
                    r = PushRecapIn.model_validate(msg.payload)
                    await supervisor.push_recap(session_id, r.concept_id, r.text)
                else:
                    raise ValueError(f"unsupported message type '{msg.type}'")
            except (ValidationError, ValueError) as e:
                await ws.send_text(WSMessage(type="error", payload={"code": ErrorCode.invalid_input,
                                                                    "message": str(e)[:300]}).model_dump_json())
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_lecturer(session_id, ws)


# ---------------------------------------------------------------- OpenAPI: include WS payloads so TS types cover them

def _openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    from . import models as m

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    comps = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in (m.WSMessage, m.QuestionOut, m.AnswerIn, m.HintRequestIn, m.FeedbackOut,
                  m.HintOut, m.TraceBackOut, m.PushRecapIn, m.SetReadyIn, m.StudentStatusOut, m.HeatmapConcept, m.Concept,
                  m.Escalation, m.RecapOut, m.RoomScoreOut, m.ModeCounts):
        s = model.model_json_schema(ref_template="#/components/schemas/{model}", mode="serialization")
        for name, d in s.pop("$defs", {}).items():
            comps[name] = _all_required(d)
        comps[model.__name__] = _all_required(s)
    app.openapi_schema = schema
    return schema


def _all_required(schema: dict[str, Any]) -> dict[str, Any]:
    """These payloads are server-produced, so every field is present (defaults are filled in)."""
    if schema.get("type") == "object" and "properties" in schema:
        schema["required"] = list(schema["properties"])
    return schema


app.openapi = _openapi  # type: ignore[method-assign]
