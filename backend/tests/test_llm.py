"""llm.call: schema parse, retry with the validation error appended, fallback, timeout mapping."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import db, llm
from app.models import ErrorCode, GraderOutput, Verdict


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=r)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)
        self.beta = SimpleNamespace(messages=self.messages)


@pytest.fixture(autouse=True)
def fresh_store():
    db._store = db.MemoryStore()
    yield
    llm.set_client(None)


GOOD = '{"score": 1, "verdict": "correct", "misconception": null, "confidence": 0.9}'


async def test_parses_valid_json():
    llm.set_client(FakeClient([GOOD]))
    out = await llm.call("grader", [{"role": "user", "content": "x"}], GraderOutput, system="s")
    assert out.verdict == Verdict.correct
    events = await db.get_store().find("events", {"agent": "grader"})
    assert events[0]["outcome"] == "ok" and events[0]["tokens_in"] == 10


async def test_tolerates_fences():
    llm.set_client(FakeClient(["```json\n" + GOOD + "\n```"]))
    out = await llm.call("grader", [{"role": "user", "content": "x"}], GraderOutput, system="s")
    assert out.score == 1


async def test_retries_with_validation_error():
    fake = FakeClient(['{"score": 7}', GOOD])
    llm.set_client(fake)
    out = await llm.call("grader", [{"role": "user", "content": "x"}], GraderOutput, system="s")
    assert out.confidence == 0.9
    retry_msgs = fake.messages.calls[1]["messages"]
    assert retry_msgs[-1]["role"] == "user" and "failed validation" in retry_msgs[-1]["content"]


async def test_fallback_after_two_failures():
    llm.set_client(FakeClient(["nope", "still nope"]))
    fb = GraderOutput(score=0, verdict=Verdict.wrong, confidence=0)
    out = await llm.call("grader", [{"role": "user", "content": "x"}], GraderOutput, system="s", fallback=fb)
    assert out is fb


async def test_bad_output_without_fallback_raises():
    llm.set_client(FakeClient(["nope", "still nope"]))
    with pytest.raises(llm.LLMError) as e:
        await llm.call("grader", [{"role": "user", "content": "x"}], GraderOutput, system="s")
    assert e.value.code == ErrorCode.llm_bad_output


async def test_timeout_maps_to_llm_timeout():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    llm.set_client(FakeClient([anthropic.APITimeoutError(request=req)]))
    with pytest.raises(llm.LLMError) as e:
        await llm.call("grader", [{"role": "user", "content": "x"}], GraderOutput, system="s")
    assert e.value.code == ErrorCode.llm_timeout


async def test_smart_tier_uses_refusal_fallback():
    fake = FakeClient([GOOD])
    llm.set_client(fake)
    await llm.call("tutor", [{"role": "user", "content": "x"}], GraderOutput, system="s", tier="smart")
    assert fake.messages.calls[0]["fallbacks"] == "default"
    assert fake.messages.calls[0]["model"] == "claude-sonnet-5-5"
