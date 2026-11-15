"""A deployment without a shared database must say so, not fail with "no such session"."""

import pytest
from fastapi.testclient import TestClient

from app import db, llm, supervisor
from app.config import get_settings
from app.main import app


@pytest.fixture
def client(monkeypatch):
    db._store = None
    db.storage_note = None
    llm.set_client(None)
    supervisor.reset()
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(get_settings(), "mongodb_uri", "")
    with TestClient(app) as c:
        yield c
    supervisor.reset()
    db._store = None
    db.storage_note = None


def test_health_explains_why_storage_is_in_memory(client):
    h = client.get("/health").json()
    assert h["storage"] == "memory" and h["storage_note"] == "MONGODB_URI is not set"


def test_off_vercel_the_in_memory_store_still_works(client):
    s = client.post("/sessions", json={"title": "Functors"})
    assert s.status_code == 200
    assert client.post("/join", json={"join_code": s.json()["join_code"], "nickname": "Priya"}).status_code == 200


def test_on_vercel_without_a_database_creating_and_joining_say_why(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    for r in (client.post("/sessions", json={"title": "x"}),
              client.post("/join", json={"join_code": "ABC234", "nickname": "x"})):
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["code"] == "not_configured"
        assert "MONGODB_URI" in err["message"] and "redeploy" in err["message"]
    assert client.get("/health").status_code == 200  # health must keep working: it is the diagnostic


@pytest.mark.parametrize("exc_name,needle", [
    ("ServerSelectionTimeoutError", "Network Access"),
    ("OperationFailure", "credentials"),
    ("ConfigurationError", "not a valid MongoDB connection string"),
    ("SomethingElse", "SomethingElse"),
])
async def test_unreachable_mongodb_is_explained_without_leaking_the_uri(monkeypatch, exc_name, needle):
    secret = "mongodb+srv://user:hunter2@cluster0.example.mongodb.net/"
    monkeypatch.setattr(get_settings(), "mongodb_uri", secret)
    exc = type(exc_name, (Exception,), {})(f"boom {secret}")

    async def fail(self):
        raise exc

    monkeypatch.setattr(db.MongoStore, "ping", fail)
    db._store = None
    store = await db.init_store()
    assert store.name == "memory"
    assert needle in db.storage_note
    assert "hunter2" not in db.storage_note and "mongodb+srv" not in db.storage_note
    db._store = None
    db.storage_note = None


async def test_connected_mongodb_clears_the_note(monkeypatch):
    monkeypatch.setattr(get_settings(), "mongodb_uri", "mongodb://example/")

    async def ok(self):
        return None

    monkeypatch.setattr(db.MongoStore, "ping", ok)
    monkeypatch.setattr(db.MongoStore, "ensure_indexes", ok)
    db.storage_note = "stale"
    store = await db.init_store()
    assert store.name == "mongodb" and db.storage_note is None
    db._store = None
    db.storage_note = None
