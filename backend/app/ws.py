"""WebSocket connection manager. Every message is a `WSMessage` envelope: {type, payload, ts}."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from .models import WSMessage, WSType

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # student_id -> sockets (a student may have several tabs open)
        self.students: dict[str, set[WebSocket]] = defaultdict(set)
        # student_id -> session_id, so we can broadcast to a session's students
        self.student_session: dict[str, str] = {}
        # session_id -> lecturer sockets
        self.lecturers: dict[str, set[WebSocket]] = defaultdict(set)

    # -- registration
    def add_student(self, session_id: str, student_id: str, ws: WebSocket) -> None:
        self.students[student_id].add(ws)
        self.student_session[student_id] = session_id

    def remove_student(self, student_id: str, ws: WebSocket) -> None:
        self.students[student_id].discard(ws)
        if not self.students[student_id]:
            self.students.pop(student_id, None)

    def add_lecturer(self, session_id: str, ws: WebSocket) -> None:
        self.lecturers[session_id].add(ws)

    def remove_lecturer(self, session_id: str, ws: WebSocket) -> None:
        self.lecturers[session_id].discard(ws)

    def online_students(self, session_id: str) -> list[str]:
        return [sid for sid, sess in self.student_session.items()
                if sess == session_id and sid in self.students]

    # -- sending
    @staticmethod
    async def _send(ws: WebSocket, msg: WSMessage) -> None:
        try:
            await ws.send_text(msg.model_dump_json())
        except Exception as e:  # noqa: BLE001 - a dead socket must not break a broadcast
            log.debug("ws send failed: %s", e)

    async def to_student(self, student_id: str, type_: WSType, payload: dict[str, Any]) -> None:
        msg = WSMessage(type=type_, payload=payload)
        await asyncio.gather(*(self._send(ws, msg) for ws in list(self.students.get(student_id, ()))))

    async def to_lecturers(self, session_id: str, type_: WSType, payload: dict[str, Any]) -> None:
        msg = WSMessage(type=type_, payload=payload)
        await asyncio.gather(*(self._send(ws, msg) for ws in list(self.lecturers.get(session_id, ()))))

    async def to_session(self, session_id: str, type_: WSType, payload: dict[str, Any]) -> None:
        """Everyone in the session: all students and all lecturer screens."""
        await asyncio.gather(
            *(self.to_student(s, type_, payload) for s in self.online_students(session_id)),
            self.to_lecturers(session_id, type_, payload),
        )


manager = ConnectionManager()
