"""On Vercel the backend is public at /api/* and requests arrive with the prefix still on.
Every route must answer at /path and at /api/path, for HTTP and WebSocket."""

import json

import pytest
from fastapi.testclient import TestClient

from app import db, llm, supervisor
from app.main import StripApiPrefix, app

from .test_flow import Inbox, demo_chunks


@pytest.fixture
def client(monkeypatch):
    db._store = None
    llm.set_client(None)
    supervisor.reset()
    monkeypatch.setattr(supervisor, "RATE_LIMIT_S", 0.0)
    with TestClient(app) as c:
        yield c
    supervisor.reset()


def test_health_works_with_and_without_the_prefix(client):
    plain, prefixed = client.get("/health"), client.get("/api/health")
    assert plain.status_code == prefixed.status_code == 200
    assert plain.json() == prefixed.json() and plain.json()["status"] == "ok"


def test_rest_routes_work_under_the_prefix(client):
    s = client.post("/api/sessions", json={"title": "Functors"}).json()
    assert len(s["join_code"]) == 6
    assert client.get(f"/api/sessions/{s['id']}").json()["title"] == "Functors"
    assert client.get(f"/sessions/{s['id']}").json()["title"] == "Functors"  # same session, no prefix
    j = client.post("/api/join", json={"join_code": s["join_code"], "nickname": "Priya"})
    assert j.status_code == 200
    assert client.get(f"/api/sessions/{s['id']}/dashboard").status_code == 200
    # Errors keep their shape under the prefix too.
    r = client.post("/api/join", json={"join_code": "ZZZZZZ", "nickname": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get("/api/sessions/nope/scribe-token").status_code == 404


def test_websocket_works_under_the_prefix(client):
    s = client.post("/api/sessions", json={"title": "Functors"}).json()
    j = client.post("/api/join", json={"join_code": s["join_code"], "nickname": "Priya"}).json()
    with client.websocket_connect(f"/api/ws/lecturer/{s['id']}") as lws, \
            client.websocket_connect(f"/api/ws/student/{j['student_id']}") as ws:
        lec, box = Inbox(lws), Inbox(ws)
        assert box.until("status")["nickname"] == "Priya"
        client.post(f"/api/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()[:6]})
        q = box.until("question")
        box.send("answer", question_id=q["question_id"], text="Maybe is a type constructor, kind star to star")
        assert box.until("feedback")["verdict"] == "correct"
        assert lec.until("heatmap")["n_students"] == 1
    # An unknown student still closes with 4404 under the prefix.
    with pytest.raises(Exception):  # noqa: B017 - starlette raises WebSocketDisconnect(4404)
        with client.websocket_connect("/api/ws/student/nope") as bad:
            Inbox(bad).until("never", limit=2)


def test_only_a_whole_leading_segment_is_stripped(client):
    assert client.get("/apix/health").status_code == 404      # not "/api" + "x/health"
    assert client.get("/health/api").status_code == 404       # only a LEADING prefix
    assert client.get("/api/api/health").status_code == 404   # one level only: becomes /api/health
    assert client.get("/api").status_code == 404              # becomes "/", which has no route


class _Recorder:
    """A bare ASGI app that records the scope it was given."""

    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)


@pytest.mark.parametrize("kind", ["http", "websocket"])
async def test_scope_is_rewritten_for_http_and_websocket_without_mutating_the_original(kind):
    rec = _Recorder()
    mw = StripApiPrefix(rec)
    original = {"type": kind, "path": "/api/sessions/abc", "raw_path": b"/api/sessions/abc", "query_string": b"x=1"}
    await mw(original, None, None)
    seen = rec.scopes[0]
    assert seen["path"] == "/sessions/abc" and seen["raw_path"] == b"/sessions/abc"
    assert seen["query_string"] == b"x=1"
    assert original["path"] == "/api/sessions/abc"  # the server's own scope is left alone


async def test_other_scope_types_and_unprefixed_paths_pass_through_untouched():
    rec = _Recorder()
    mw = StripApiPrefix(rec)
    lifespan = {"type": "lifespan"}
    plain = {"type": "http", "path": "/health", "raw_path": b"/health"}
    bare = {"type": "http", "path": "/api", "raw_path": b"/api"}
    for sc in (lifespan, plain, bare):
        await mw(sc, None, None)
    assert rec.scopes[0] is lifespan and rec.scopes[1] is plain
    assert rec.scopes[2]["path"] == "/" and rec.scopes[2]["raw_path"] == b"/"
