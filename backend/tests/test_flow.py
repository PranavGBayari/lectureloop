"""Phase 2 gate, in-process: transcript -> concepts -> questions -> grades -> streamed feedback,
including hints, a trace-back to a prerequisite, and the mode switch."""

import json

import pytest
from fastapi.testclient import TestClient

from app import db, llm, supervisor
from app.main import app


@pytest.fixture
def client(monkeypatch):
    db._store = None
    llm.set_client(None)
    supervisor.reset()
    monkeypatch.setattr(supervisor, "RATE_LIMIT_S", 0.0)
    with TestClient(app) as c:
        yield c
    supervisor.reset()


def demo_chunks():
    d = supervisor.load_demo()
    return [{k: c[k] for k in ("t_start", "t_end", "text")} for c in d["chunks"]]


class Inbox:
    def __init__(self, ws):
        self.ws, self.seen = ws, []

    def until(self, type_, pred=lambda p: True, limit=200):
        for _ in range(limit):
            m = json.loads(self.ws.receive_text())
            self.seen.append(m)
            if m["type"] == type_ and pred(m["payload"]):
                return m["payload"]
        raise AssertionError(f"no {type_}; saw {[m['type'] for m in self.seen[-20:]]}")

    def send(self, type_, **payload):
        self.ws.send_text(json.dumps({"type": type_, "payload": payload}))


def test_basic_rest(client):
    s = client.post("/sessions", json={"title": "Functors"}).json()
    assert len(s["join_code"]) == 6
    r = client.post("/join", json={"join_code": s["join_code"].lower(), "nickname": "Priya"})
    assert r.status_code == 200
    r = client.post("/join", json={"join_code": "ZZZZZZ", "nickname": "x"})
    assert r.json() == {"error": {"code": "not_found", "message": "No lecture with that join code"}}
    r = client.post(f"/sessions/{s['id']}/replay", json={"speed": 99})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_input"


def test_full_loop(client):
    s = client.post("/sessions", json={"title": "Functors"}).json()
    j = client.post("/join", json={"join_code": s["join_code"], "nickname": "Priya"}).json()

    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        status = box.until("status")
        assert status["mode"] == "normal" and status["ready"] is True

        r = client.post(f"/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()})
        assert r.json() == {"accepted": 16}

        c1 = box.until("concept_closed")["concept"]
        assert c1["name"] == "Type constructors" and c1["t_end"] == 46

        q1 = box.until("question")
        assert q1["concept_id"] == c1["id"]
        assert "rubric" not in q1 and "hints" not in q1  # never sent to students
        assert q1["source_quote"]

        # Wrong answer on the first concept: no prerequisite, so the move is a hint.
        box.send("answer", question_id=q1["question_id"], text="no idea")
        deltas = []
        fb = None
        while fb is None:
            m = json.loads(ws.receive_text())
            if m["type"] == "feedback_delta":
                deltas.append(m["payload"]["delta"])
            elif m["type"] == "feedback":
                fb = m["payload"]
        assert fb["verdict"] == "wrong" and fb["next"] == "give_hint"
        assert len(deltas) > 1 and "".join(deltas).strip() == fb["message"]  # streamed
        assert box.until("hint")["level"] == 1

        # Correct answer: advance to the next concept's question.
        box.send("answer", question_id=q1["question_id"],
                 text="Maybe is a type constructor; it needs a type argument, kind star to star")
        fb = box.until("feedback")
        assert fb["verdict"] == "correct" and fb["next"] == "advance" and fb["mastery"] > 0.3

        q2 = box.until("question", lambda p: p["concept_id"] != c1["id"])
        # Wrong answer on Functors -> trace back to Type constructors.
        box.send("answer", question_id=q2["question_id"], text="it just can, because of reasons")
        fb = box.until("feedback")
        assert fb["next"] == "trace_back"
        tb = box.until("trace_back")
        assert tb["to_concept"] == "Type constructors" and tb["at"] == "0:00"
        q3 = box.until("question")
        assert q3["concept_id"] == c1["id"] and q3["difficulty"] == 1

    d = client.get(f"/sessions/{s['id']}/dashboard").json()
    names = [c["name"] for c in d["concepts"]]
    assert names[:3] == ["Type constructors", "Functors and fmap", "Functor laws"]
    tc = d["concepts"][0]
    assert tc["n_active"] == 1 and tc["avg_mastery"] > 0
    assert tc["n_answers"] == 2 and tc["n_struggling"] == 1  # "no idea", then correct
    fn = d["concepts"][1]
    assert fn["n_struggling"] == 1 and len(fn["misconceptions"]) <= 3
    assert sum(d["modes"].values()) == 1


def test_mode_switches_to_support(client):
    s = client.post("/sessions", json={"title": "Functors"}).json()
    j = client.post("/join", json={"join_code": s["join_code"], "nickname": "Sam"}).json()
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        client.post(f"/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()[:6]})
        q = box.until("question")
        box.send("answer", question_id=q["question_id"], text="dunno")
        assert box.until("feedback")["mode"] == "normal"
        box.until("hint")
        box.send("answer", question_id=q["question_id"], text="still dunno")
        assert box.until("feedback")["mode"] == "support"
    d = client.get(f"/sessions/{s['id']}/dashboard").json()
    assert d["modes"] == {"normal": 0, "support": 1, "challenge": 0}


def test_not_ready_student_waits(client):
    s = client.post("/sessions", json={"title": "Functors"}).json()
    j = client.post("/join", json={"join_code": s["join_code"], "nickname": "Ana"}).json()
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        box.send("set_ready", ready=False)
        box.until("status", lambda p: p["ready"] is False)
        client.post(f"/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()[:6]})
        box.until("concept_closed")
        assert not any(m["type"] == "question" for m in box.seen)
        box.send("set_ready", ready=True)
        assert box.until("question")["concept_id"]


def test_bad_messages(client):
    s = client.post("/sessions", json={"title": "Functors"}).json()
    j = client.post("/join", json={"join_code": s["join_code"], "nickname": "Priya"}).json()
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        ws.send_text("not json")
        assert box.until("error")["code"] == "invalid_input"
        box.send("answer", question_id="nope", text="x")
        assert box.until("error")["code"] == "not_found"

    with client.websocket_connect("/ws/student/doesnotexist") as ws:
        assert json.loads(ws.receive_text())["payload"]["code"] == "not_found"
