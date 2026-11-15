"""Single entry point for every Claude call: `await llm.call(agent, messages, schema)`.

Handles, in code:
- timeouts (per-call, mapped to `llm_timeout`)
- transport retries (SDK: 408/409/429/5xx/connection errors)
- schema validation with Pydantic, one retry with the validation error appended,
  then a safe fallback (or `llm_bad_output` if the caller gave none)
- refusal stop reasons
- event logging (latency, tokens, outcome) to the `events` collection
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .config import get_settings
from .db import get_store
from .models import ErrorCode, Event

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Tier = Literal["smart", "fast"]


class InsufficientSource(Exception):
    """The agent said the transcript/slides don't contain what it needs. Callers skip, never guess."""


class LLMError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_client: Any = None

# Which lecture the current task is working for, so every event row can be attributed to a session.
current_session: ContextVar[str | None] = ContextVar("current_session", default=None)


def get_client() -> Any:
    global _client
    if _client is None:
        s = get_settings()
        if s.llm_provider == "fake":
            from .fake_llm import FakeAnthropic

            _client = FakeAnthropic()
            log.warning("LLM_PROVIDER=fake: using the scripted offline model, not Claude")
            return _client
        _client = anthropic.AsyncAnthropic(
            api_key=s.anthropic_api_key or None,  # None lets the SDK resolve other credentials
            timeout=s.llm_timeout_s,
            max_retries=2,
        )
    return _client


def set_client(client: Any) -> None:
    """Test hook: inject a fake client."""
    global _client
    _client = client


def model_for(tier: Tier) -> str:
    s = get_settings()
    return s.model_smart if tier == "smart" else s.model_fast


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse the first JSON object in `text`, tolerating ```json fences or stray prose."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "\n\nRespond with a single JSON object and nothing else. "
        "It must validate against this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


async def log_event(event: Event) -> None:
    """Append to the observability log. Used by llm calls and by every guardrail decision."""
    if event.session_id is None:
        event.session_id = current_session.get()
    try:
        await get_store().insert("events", event.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001 - logging must never break a turn
        log.warning("event log failed: %s", e)


async def _create(tier: Tier, system: str, messages: list[dict[str, Any]], max_tokens: int):
    client = get_client()
    kwargs: dict[str, Any] = dict(
        model=model_for(tier),
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        # Low effort keeps live-lecture latency down; these are short, well-specified tasks.
        output_config={"effort": "low"},
    )
    if tier == "smart":
        # Server-side refusal fallback (Sonnet 5.5): reroute a policy decline instead of failing.
        return await client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    return await client.messages.create(**kwargs)


def _text_of(response: Any) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")


async def call(
    agent: str,
    messages: list[dict[str, Any]],
    schema: type[T],
    *,
    system: str,
    tier: Tier = "fast",
    max_tokens: int = 2048,
    fallback: T | None = None,
    step: str = "call",
) -> T:
    """Call Claude and return a validated `schema` instance.

    Raises LLMError(llm_timeout | llm_bad_output) unless a `fallback` is given for bad output.
    """
    full_system = system + _schema_instruction(schema)
    convo = list(messages)
    last_error = ""

    for attempt in (1, 2):
        t0 = time.perf_counter()
        try:
            resp = await _create(tier, full_system, convo, max_tokens)
        except anthropic.APITimeoutError as e:
            await log_event(Event(agent=agent, step=step, latency_ms=_ms(t0), outcome="timeout"))
            raise LLMError(ErrorCode.llm_timeout, f"{agent} timed out") from e
        except anthropic.APIConnectionError as e:
            await log_event(Event(agent=agent, step=step, latency_ms=_ms(t0), outcome="connection_error"))
            raise LLMError(ErrorCode.llm_timeout, f"{agent} could not reach the model") from e
        except anthropic.APIStatusError as e:
            await log_event(Event(agent=agent, step=step, latency_ms=_ms(t0), outcome=f"http_{e.status_code}"))
            raise LLMError(ErrorCode.llm_bad_output, f"{agent} API error {e.status_code}") from e

        usage = getattr(resp, "usage", None)
        ev = Event(
            agent=agent,
            step=f"{step}#{attempt}",
            latency_ms=_ms(t0),
            tokens_in=getattr(usage, "input_tokens", 0) or 0,
            tokens_out=getattr(usage, "output_tokens", 0) or 0,
            outcome="ok",
        )

        if getattr(resp, "stop_reason", None) == "refusal":
            ev.outcome = "refusal"
            await log_event(ev)
            last_error = "model refused"
            break

        text = _text_of(resp)
        try:
            data = extract_json(text)
            if isinstance(data, dict) and data.get("insufficient_source") is True:
                ev.outcome = "insufficient_source"
                await log_event(ev)
                if fallback is not None:
                    return fallback  # callers that can degrade safely never see the exception
                raise InsufficientSource(agent)
            parsed = schema.model_validate(data)
            ev.guardrail = "schema_validation"  # a passed check; failures log "schema_error" below
            await log_event(ev)
            return parsed
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)[:800]
            ev.outcome = "schema_error"
            ev.guardrail = "schema_validation"
            await log_event(ev)
            # Retry once, showing the model its own output and what was wrong with it.
            convo = [
                *messages,
                {"role": "assistant", "content": text or "(empty)"},
                {
                    "role": "user",
                    "content": "That output failed validation:\n"
                    f"{last_error}\nReturn only the corrected JSON object.",
                },
            ]

    if fallback is not None:
        await log_event(Event(agent=agent, step=step, outcome="fallback", guardrail="schema_validation"))
        return fallback
    raise LLMError(ErrorCode.llm_bad_output, f"{agent} returned invalid output: {last_error}")


async def stream_text(
    agent: str,
    messages: list[dict[str, Any]],
    *,
    system: str,
    tier: Tier = "smart",
    max_tokens: int = 1024,
    step: str = "stream",
) -> AsyncIterator[str]:
    """Stream plain-text deltas. Logs one event when the stream ends. Raises LLMError."""
    client = get_client()
    kwargs: dict[str, Any] = dict(
        model=model_for(tier),
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": "low"},
    )
    if tier == "smart":
        ctx = client.beta.messages.stream(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    else:
        ctx = client.messages.stream(**kwargs)
    t0 = time.perf_counter()
    ev = Event(agent=agent, step=step, outcome="ok")
    try:
        async with ctx as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            usage = getattr(final, "usage", None)
            ev.tokens_in = getattr(usage, "input_tokens", 0) or 0
            ev.tokens_out = getattr(usage, "output_tokens", 0) or 0
            stop = getattr(final, "stop_reason", None)
            if stop in ("refusal", "max_tokens"):
                ev.outcome = stop
    except anthropic.APITimeoutError as e:
        ev.outcome = "timeout"
        raise LLMError(ErrorCode.llm_timeout, f"{agent} timed out") from e
    except anthropic.APIConnectionError as e:
        ev.outcome = "connection_error"
        raise LLMError(ErrorCode.llm_timeout, f"{agent} could not reach the model") from e
    except anthropic.APIStatusError as e:
        ev.outcome = f"http_{e.status_code}"
        raise LLMError(ErrorCode.llm_bad_output, f"{agent} API error {e.status_code}") from e
    finally:
        ev.latency_ms = _ms(t0)
        await log_event(ev)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
