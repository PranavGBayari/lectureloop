"""Storage behind one small async interface.

`MongoStore` uses Motor (MongoDB Atlas). `MemoryStore` is the fallback when MONGODB_URI is
empty, so the demo runs with zero setup. Both store plain dicts produced by Pydantic
`model_dump(mode="json")`, so the shapes are identical.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Protocol

from .config import get_settings

log = logging.getLogger(__name__)

COLLECTIONS = (
    "sessions",
    "transcript_chunks",
    "concepts",
    "students",
    "questions",
    "answers",
    "mastery",
    "escalations",
    "events",
)


class Store(Protocol):
    async def insert(self, coll: str, doc: dict[str, Any]) -> None: ...
    async def find_one(self, coll: str, query: dict[str, Any]) -> dict[str, Any] | None: ...
    async def find(
        self, coll: str, query: dict[str, Any], limit: int = 0
    ) -> list[dict[str, Any]]: ...
    async def update_one(
        self, coll: str, query: dict[str, Any], set_: dict[str, Any], upsert: bool = False
    ) -> None: ...
    async def count(self, coll: str, query: dict[str, Any]) -> int: ...


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(doc.get(k) == v for k, v in query.items())


class MemoryStore:
    """Equality-only queries; enough for the app's access patterns."""

    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {c: [] for c in COLLECTIONS}

    async def insert(self, coll: str, doc: dict[str, Any]) -> None:
        self._data.setdefault(coll, []).append(copy.deepcopy(doc))

    async def find_one(self, coll: str, query: dict[str, Any]) -> dict[str, Any] | None:
        for d in self._data.get(coll, []):
            if _matches(d, query):
                return copy.deepcopy(d)
        return None

    async def find(self, coll: str, query: dict[str, Any], limit: int = 0) -> list[dict[str, Any]]:
        out = [copy.deepcopy(d) for d in self._data.get(coll, []) if _matches(d, query)]
        return out[:limit] if limit else out

    async def update_one(
        self, coll: str, query: dict[str, Any], set_: dict[str, Any], upsert: bool = False
    ) -> None:
        for d in self._data.get(coll, []):
            if _matches(d, query):
                d.update(copy.deepcopy(set_))
                return
        if upsert:
            await self.insert(coll, {**query, **set_})

    async def count(self, coll: str, query: dict[str, Any]) -> int:
        return sum(1 for d in self._data.get(coll, []) if _matches(d, query))


class MongoStore:
    name = "mongodb"

    def __init__(self, uri: str, db_name: str) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        self._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self._db = self._client[db_name]

    async def ping(self) -> None:
        await self._client.admin.command("ping")

    async def ensure_indexes(self) -> None:
        await self._db.sessions.create_index("id", unique=True)
        await self._db.sessions.create_index("join_code", unique=True)
        await self._db.students.create_index("id", unique=True)
        await self._db.transcript_chunks.create_index([("session_id", 1), ("seq", 1)])
        await self._db.mastery.create_index([("student_id", 1), ("concept_id", 1)], unique=True)

    async def insert(self, coll: str, doc: dict[str, Any]) -> None:
        await self._db[coll].insert_one(copy.deepcopy(doc))

    async def find_one(self, coll: str, query: dict[str, Any]) -> dict[str, Any] | None:
        return await self._db[coll].find_one(query, {"_id": 0})

    async def find(self, coll: str, query: dict[str, Any], limit: int = 0) -> list[dict[str, Any]]:
        cursor = self._db[coll].find(query, {"_id": 0})
        if limit:
            cursor = cursor.limit(limit)
        return await cursor.to_list(length=None)

    async def update_one(
        self, coll: str, query: dict[str, Any], set_: dict[str, Any], upsert: bool = False
    ) -> None:
        await self._db[coll].update_one(query, {"$set": set_}, upsert=upsert)

    async def count(self, coll: str, query: dict[str, Any]) -> int:
        return await self._db[coll].count_documents(query)


_store: Store | None = None
# Why the in-memory store is in use (None when MongoDB is). Shown by GET /health so a deployment that
# silently lost its database is easy to diagnose.
storage_note: str | None = None


def _explain(e: Exception) -> str:
    """A short, secret-free reason a MongoDB connection failed (the URI contains the password)."""
    name = type(e).__name__
    if name == "ServerSelectionTimeoutError":
        return ("MongoDB did not answer: check the cluster is running and that Atlas Network Access allows "
                "the server's IP (Vercel uses changing IPs, so allow 0.0.0.0/0)")
    if name in ("OperationFailure", "AuthenticationFailed") or "auth" in str(e).lower():
        return "MongoDB rejected the credentials: check the username and password in MONGODB_URI"
    if name in ("ConfigurationError", "InvalidURI"):
        return "MONGODB_URI is not a valid MongoDB connection string"
    return f"could not connect to MongoDB ({name})"


async def init_store() -> Store:
    """Connect to MongoDB if configured; fall back to memory if it's empty or unreachable."""
    global _store, storage_note
    s = get_settings()
    if s.mongodb_uri:
        try:
            mongo = MongoStore(s.mongodb_uri, s.mongodb_db)
            await mongo.ping()
            await mongo.ensure_indexes()
            _store = mongo
            storage_note = None
            log.info("storage: MongoDB (%s)", s.mongodb_db)
            return _store
        except Exception as e:  # noqa: BLE001 - any connect failure means fall back
            storage_note = _explain(e)
            log.warning("storage: %s; using in-memory store (%s)", storage_note, type(e).__name__)
    else:
        storage_note = "MONGODB_URI is not set"
    _store = MemoryStore()
    log.info("storage: in-memory")
    return _store


def get_store() -> Store:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store
