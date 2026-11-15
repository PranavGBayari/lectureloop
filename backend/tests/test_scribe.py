"""ElevenLabs Scribe token endpoint: the key stays on the server, errors are clear, nothing is cached."""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, llm, scribe, supervisor
from app.config import get_settings
from app.main import app

KEY = "xi-secret-key-123"


@pytest.fixture
def client(monkeypatch):
    db._store = None
    llm.set_client(None)
    supervisor.reset()
    monkeypatch.setattr(scribe, "_transport", None)
    with TestClient(app) as c:
        yield c
    supervisor.reset()


def mock_elevenlabs(monkeypatch, handler):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(scribe, "_transport", httpx.MockTransport(wrapped))
    return seen


def session(client):
    return client.post("/sessions", json={"title": "Functors"}).json()


def test_health_reports_whether_elevenlabs_is_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "")
    assert client.get("/health").json()["elevenlabs"] is False
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", KEY)
    h = client.get("/health").json()
    assert h["elevenlabs"] is True and KEY not in str(h)  # a flag, never the key


def test_token_requires_the_key_to_be_set(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "")
    s = session(client)
    r = client.get(f"/sessions/{s['id']}/scribe-token")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "not_configured"
    assert "ELEVENLABS_API_KEY" in r.json()["error"]["message"]


def test_token_exchange(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", KEY)
    seen = mock_elevenlabs(monkeypatch, lambda req: httpx.Response(200, json={"token": "sutkn_abc123"}))
    s = session(client)
    r = client.get(f"/sessions/{s['id']}/scribe-token")
    assert r.status_code == 200 and r.json() == {"token": "sutkn_abc123"}
    assert r.headers["cache-control"] == "no-store"
    # The key goes to ElevenLabs in the header, to the right endpoint, and nowhere else.
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe"
    assert seen[0].headers["xi-api-key"] == KEY
    assert KEY not in r.text and KEY not in str(dict(r.headers))


@pytest.mark.parametrize("status,needle", [(401, "rejected the API key"), (403, "rejected the API key"),
                                           (429, "rate limiting"), (500, "HTTP 500")])
def test_upstream_errors_are_clear_and_leak_nothing(client, monkeypatch, status, needle):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", KEY)
    mock_elevenlabs(monkeypatch, lambda req: httpx.Response(status, text=f"boom, key was {KEY}"))
    r = client.get(f"/sessions/{client.post('/sessions', json={'title': 'x'}).json()['id']}/scribe-token")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"
    assert needle in r.json()["error"]["message"]
    assert KEY not in r.text  # the upstream body is logged server-side, never returned


def test_network_failures(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", KEY)
    s = session(client)

    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    mock_elevenlabs(monkeypatch, timeout)
    r = client.get(f"/sessions/{s['id']}/scribe-token")
    assert r.status_code == 504 and r.json()["error"]["code"] == "llm_timeout"

    def refused(req):
        raise httpx.ConnectError("no route", request=req)

    mock_elevenlabs(monkeypatch, refused)
    r = client.get(f"/sessions/{s['id']}/scribe-token")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": 5}, ["x"]])
def test_response_without_a_usable_token(client, monkeypatch, body):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", KEY)
    mock_elevenlabs(monkeypatch, lambda req: httpx.Response(200, json=body))
    r = client.get(f"/sessions/{session(client)['id']}/scribe-token")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"


def test_unknown_or_ended_session(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", KEY)
    seen = mock_elevenlabs(monkeypatch, lambda req: httpx.Response(200, json={"token": "t"}))
    assert client.get("/sessions/nope/scribe-token").status_code == 404
    s = session(client)
    client.post(f"/sessions/{s['id']}/end")
    for _ in range(100):  # the worker marks the session ended shortly after /end returns
        if client.get(f"/sessions/{s['id']}").json()["status"] == "ended":
            break
        time.sleep(0.05)
    r = client.get(f"/sessions/{s['id']}/scribe-token")
    assert r.status_code == 409 and "ended" in r.json()["error"]["message"]
    assert seen == []  # no token is minted for a session that cannot use it
