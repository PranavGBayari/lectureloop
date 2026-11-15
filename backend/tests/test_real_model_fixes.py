"""Regressions found when running against the real models: an agent answering
{"insufficient_source": true} must never reach a student as raw text or crash a turn, the tutor
must see the lecture material, and quotes and feedback carry real lecture times."""

import json

import pytest
from fastapi.testclient import TestClient

from app import db, fake_llm, llm, prompts, supervisor
from app.agents import tutor
from app.main import app

from .test_flow import Inbox, demo_chunks

RAW = '{"insufficient_source": true}'


@pytest.fixture
def client(monkeypatch):
    db._store = None
    llm.set_client(None)
    supervisor.reset()
    monkeypatch.setattr(supervisor, "RATE_LIMIT_S", 0.0)
    with TestClient(app) as c:
        yield c
    supervisor.reset()


def first_question(client, n_chunks=6):
    s = client.post("/sessions", json={"title": "Functors"}).json()
    j = client.post("/join", json={"join_code": s["join_code"], "nickname": "Priya"}).json()
    return s, j


def answer_and_collect(ws, box, question_id, text):
    """Send an answer; return (streamed deltas, final feedback payload)."""
    box.send("answer", question_id=question_id, text=text)
    deltas = []
    while True:
        m = json.loads(ws.receive_text())
        if m["type"] == "feedback_delta":
            deltas.append(m["payload"]["delta"])
        elif m["type"] == "feedback":
            return deltas, m["payload"]


@pytest.mark.parametrize("raw", [RAW, f"```json\n{RAW}\n```", '{"message": "hi"}'])
def test_tutor_json_never_reaches_the_student(client, monkeypatch, raw):
    monkeypatch.setattr(fake_llm, "TUTOR", {k: raw for k in fake_llm.TUTOR})
    s, j = first_question(client)
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        client.post(f"/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()[:6]})
        q = box.until("question")
        deltas, fb = answer_and_collect(ws, box, q["question_id"], "no idea")
    shown = "".join(deltas)
    assert fb["message"] == tutor.FALLBACK[supervisor.NextMove.give_hint]  # the friendly canned message
    assert shown.strip() == fb["message"]
    for text in (shown, fb["message"]):
        assert "insufficient_source" not in text and "{" not in text and "```" not in text


def test_tutor_is_given_the_transcript_span_and_no_json_rule(client, monkeypatch):
    seen = []
    real = fake_llm._respond
    monkeypatch.setattr(fake_llm, "_respond", lambda system, messages: (seen.append((system, messages)), real(system, messages))[1])
    s, j = first_question(client)
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        client.post(f"/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()[:6]})
        q = box.until("question")
        answer_and_collect(ws, box, q["question_id"], "no idea")
    system, messages = next((s_, m) for s_, m in seen if "You are the tutor" in s_)
    user = messages[0]["content"]
    assert "<transcript_span>" in user and "[seq 0 |" in user and "<slides>" in user
    # The shared "return insufficient_source JSON" rule is for JSON agents only.
    assert "insufficient_source" not in system and "never JSON" in system
    assert "insufficient_source" in prompts.load("grader")


def test_grader_insufficient_source_does_not_break_the_turn(client, monkeypatch):
    monkeypatch.setattr(fake_llm, "_grade", lambda user: {"insufficient_source": True})
    s, j = first_question(client)
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        client.post(f"/sessions/{s['id']}/transcript", json={"chunks": demo_chunks()[:6]})
        q = box.until("question")
        deltas, fb = answer_and_collect(ws, box, q["question_id"], "Maybe needs a type argument")
    # Neutral grade, a normal message, and the answer goes to a TA because confidence is zero.
    assert fb["verdict"] == "partial" and "insufficient_source" not in fb["message"] and fb["message"]
    queue = client.get(f"/sessions/{s['id']}/escalations").json()
    assert [e["type"] for e in queue] == ["low_confidence_grade"]


def test_tutor_clean():
    assert tutor.clean(RAW) is None
    assert tutor.clean("") is None
    assert tutor.clean("Half a sent") is None
    assert tutor.clean("Good. Now think about `fmap id`.") == "Good. Now think about `fmap id`."
    cut = "You spotted that the shape stays the same. Now look at what happens to the val"
    assert tutor.clean(cut) == "You spotted that the shape stays the same."


def test_quote_and_feedback_carry_lecture_time(client):
    chunks = demo_chunks()
    s, j = first_question(client)
    with client.websocket_connect(f"/ws/student/{j['student_id']}") as ws:
        box = Inbox(ws)
        box.until("status")
        client.post(f"/sessions/{s['id']}/transcript", json={"chunks": chunks})
        q1 = box.until("question")
        _, fb = answer_and_collect(ws, box, q1["question_id"],
                                   "Maybe is a type constructor; it needs a type argument, kind star to star")
        q2 = box.until("question", lambda p: p["concept_id"] != q1["concept_id"])

    def said_at(quote):
        return next(c["t_start"] for c in chunks if quote.lower() in c["text"].lower())

    # The timestamp is where the quoted words were actually said, not the start of the concept.
    assert q1["source_t"] == said_at(q1["source_quote"])
    assert q2["source_t"] == said_at(q2["source_quote"]) and q2["source_t"] >= 46
    assert fb["lecture_t"] == chunks[-1]["t_end"]  # the whole transcript was posted at once
