"""ElevenLabs Scribe v2 Realtime: mint a single-use token for the lecturer's browser.

The browser streams microphone audio straight to ElevenLabs over a WebSocket, so it needs
credentials, but the API key must never reach it. Instead the backend exchanges the key for a
single-use token (valid 15 minutes, consumed on connect) and returns only that.
"""

from __future__ import annotations

import logging

import httpx

from .config import get_settings
from .models import ErrorCode

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe"
TIMEOUT_S = 10.0

# Test hook: an httpx transport (e.g. httpx.MockTransport) to use instead of the network.
_transport: httpx.AsyncBaseTransport | None = None


class ScribeError(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def configured() -> bool:
    return bool(get_settings().elevenlabs_api_key.strip())


async def create_token() -> str:
    """Ask ElevenLabs for a single-use realtime Scribe token. Raises ScribeError; never exposes the key."""
    key = get_settings().elevenlabs_api_key.strip()
    if not key:
        raise ScribeError(503, ErrorCode.not_configured,
                          "ElevenLabs transcription is not set up on this server (ELEVENLABS_API_KEY is empty).")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=_transport) as client:
            r = await client.post(TOKEN_URL, headers={"xi-api-key": key})
    except httpx.TimeoutException as e:
        raise ScribeError(504, ErrorCode.llm_timeout, "ElevenLabs did not answer in time.") from e
    except httpx.HTTPError as e:
        raise ScribeError(502, ErrorCode.upstream_error, "Could not reach ElevenLabs.") from e

    if r.status_code in (401, 403):
        raise ScribeError(502, ErrorCode.upstream_error,
                          "ElevenLabs rejected the API key (check ELEVENLABS_API_KEY and that it can use Scribe).")
    if r.status_code == 429:
        raise ScribeError(502, ErrorCode.upstream_error, "ElevenLabs is rate limiting token requests. Try again shortly.")
    if r.status_code != 200:
        # The upstream body can echo request details, so it goes to the log (truncated), not to the browser.
        log.warning("ElevenLabs token request failed: HTTP %s %s", r.status_code, r.text[:200])
        raise ScribeError(502, ErrorCode.upstream_error, f"ElevenLabs returned an error (HTTP {r.status_code}).")
    try:
        token = r.json()["token"]
    except (ValueError, KeyError, TypeError) as e:
        raise ScribeError(502, ErrorCode.upstream_error, "ElevenLabs sent a response without a token.") from e
    if not isinstance(token, str) or not token:
        raise ScribeError(502, ErrorCode.upstream_error, "ElevenLabs sent an empty token.")
    return token
