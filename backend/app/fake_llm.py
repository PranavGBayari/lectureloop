"""A scripted stand-in for the Anthropic client. Only used when LLM_PROVIDER=fake.

It knows the demo lecture (app/data/lecture_transcript.json) well enough to segment it, write grounded
questions, grade by keyword and stream tutor messages, so tests and UI work run without an API key
or network. It mimics the SDK surface that llm.py uses: messages.create / messages.stream and the
beta equivalents.
"""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any

LATENCY_S = 0.15

# (name, keywords) in priority order: the first topic whose keyword appears in a chunk wins.
TOPICS = [
    ("Applicatives", ["applicative", "function itself is inside", "angle star", "wrapped function"]),
    ("Functor laws", ["law"]),
    ("Functors and fmap", ["functor type class", "f a and f b", "what does fmap do", "for maybe, fmap"]),
    ("Type constructors", ["type constructor", "kind", "is a type", "glossing over"]),
]
ORDER = ["Type constructors", "Functors and fmap", "Functor laws", "Applicatives"]
PREREQS = {
    "Functors and fmap": ["Type constructors"],
    "Functor laws": ["Functors and fmap"],
    "Applicatives": ["Functors and fmap", "Type constructors"],
}

QUESTIONS: dict[str, list[dict[str, Any]]] = {
    "Type constructors": [
        dict(text="Why is `Maybe` on its own not a type you can give a value?", kind="explain",
             keys=["argument", "constructor", "star to star", "* -> *", "needs a type", "takes a type"]),
        dict(text="What kind does `Either` have, and why?", kind="recall",
             keys=["two", "star to star to star", "* -> * -> *", "two type arguments"]),
    ],
    "Functors and fmap": [
        dict(text="Why can't `fmap` change the shape of a list?", kind="explain",
             keys=["each element", "every element", "structure", "shape", "values inside", "law"]),
        dict(text="What does `fmap (+1) Nothing` give, and why?", kind="apply",
             keys=["nothing", "no value", "unchanged", "structure"]),
        dict(text="Why can `Maybe` be a Functor but `Int` cannot?", kind="explain",
             keys=["star to star", "* -> *", "type constructor", "kind", "argument"]),
    ],
    "Functor laws": [
        dict(text="An `fmap` for lists that reverses the list type checks. Which law does it break, and why?",
             kind="spot-the-bug", keys=["identity", "fmap id", "same structure", "reverse"]),
        dict(text="In your own words, what does the identity law `fmap id = id` guarantee?", kind="explain",
             keys=["same", "structure", "unchanged", "identity"]),
    ],
    "Applicatives": [
        dict(text="Why can't plain `fmap` apply `Just (+3)` to `Just 4`?", kind="explain",
             keys=["function", "inside", "wrapped", "plain function"]),
        dict(text="What does `pure` do in Applicative?", kind="recall",
             keys=["wrap", "minimal", "structure", "value"]),
    ],
}


def _agent(system: str) -> str:
    m = re.search(r"You are the (\w[\w ]*?) for LectureLoop", system)
    return (m.group(1) if m else "unknown").strip()


def _user_text(messages: list[dict[str, Any]]) -> str:
    return messages[0]["content"] if messages else ""


def _topic(text: str) -> str | None:
    t = text.lower()
    for name, keys in TOPICS:
        if any(k in t for k in keys):
            return name
    return None


_CHUNK_RE = re.compile(r"\[seq (\d+) \| [^\]]*\] (.*)")


def _segment(user: str) -> dict[str, Any]:
    block = re.search(r"<open_concept[^>]*>\n(.*?)\n</open_concept>", user, re.DOTALL)
    chunks = [(int(s), t) for s, t in _CHUNK_RE.findall(block.group(1) if block else "")]
    earlier = re.search(r"<earlier_concepts>\n(.*?)\n</earlier_concepts>", user, re.DOTALL)
    earlier_names = [] if not earlier or earlier.group(1) == "(none)" else earlier.group(1).split("\n")
    force = "<force_close>" in user
    if not chunks:
        return {"boundary": False}

    labels, last = [], None
    for _, text in chunks:
        last = _topic(text) or last
        labels.append(last)
    first = labels[0] or (ORDER[len(earlier_names)] if len(earlier_names) < len(ORDER) else "Wrap-up")

    def describe(name: str, texts: list[str]) -> dict[str, Any]:
        quote = " ".join(texts[0].split()[:12])
        return {"name": name, "summary": f"The lecturer explained {name.lower()}.", "source_quote": quote}

    if force:
        return {"boundary": True, "new_topic_starts_at_seq": None, "next_topic": None,
                "concept": describe(first, [t for _, t in chunks]),
                "prereqs": [p for p in PREREQS.get(first, []) if p in earlier_names]}
    for (seq, _), label in zip(chunks, labels):
        if label and label != first:
            finished = [t for s, t in chunks if s < seq]
            return {"boundary": True, "new_topic_starts_at_seq": seq, "next_topic": label,
                    "concept": describe(first, finished),
                    "prereqs": [p for p in PREREQS.get(first, []) if p in earlier_names]}
    return {"boundary": False}


def _write_question(user: str) -> dict[str, Any]:
    name = re.search(r'<concept name="([^"]*)"', user)
    name = name.group(1) if name else "this concept"
    span = re.search(r"<transcript_span>\n(.*?)\n</transcript_span>", user, re.DOTALL)
    texts = [t for _, t in _CHUNK_RE.findall(span.group(1) if span else "")]
    asked = re.search(r"<already_asked>\n(.*?)\n</already_asked>", user, re.DOTALL)
    asked_text = asked.group(1) if asked else ""
    diff = re.search(r'difficulty="(\d)"', user)
    difficulty = int(diff.group(1)) if diff else 2

    bank = QUESTIONS.get(name, [])
    pick = next((q for q in bank if q["text"] not in asked_text), None)
    if pick is None:
        pick = dict(text=f"In your own words, what was the main idea of {name}?", kind="explain",
                    keys=name.lower().split())
    # Quote a sentence that actually contains one of the keys if possible, else the first chunk.
    quote_src = next((t for t in texts if any(k in t.lower() for k in pick["keys"])), texts[0] if texts else name)
    quote = " ".join(quote_src.split()[:12])
    return {
        "text": pick["text"],
        "kind": pick["kind"],
        "difficulty": difficulty,
        "rubric": f"A correct answer explains {name.lower()}. Key ideas: " + "; ".join(pick["keys"]),
        "source_quote": quote,
        "hints": [
            f"Think back to what the lecturer said about {name.lower()}.",
            f"Focus on this: \"{quote}\".",
            "Which single word in that quote does the question hinge on?",
        ],
    }


def _grade(user: str) -> dict[str, Any]:
    rubric = re.search(r"<rubric>(.*?)</rubric>", user, re.DOTALL)
    keys = []
    if rubric and "Key ideas:" in rubric.group(1):
        keys = [k.strip().lower() for k in rubric.group(1).split("Key ideas:")[1].split(";") if k.strip()]
    answer = re.search(r"<student_answer>\n(.*?)\n</student_answer>", user, re.DOTALL)
    a = (answer.group(1) if answer else "").lower()
    prereqs = re.search(r"<prerequisites>\n(.*?)\n</prerequisites>", user, re.DOTALL)
    prereq_names = [] if not prereqs or prereqs.group(1) == "(none)" else prereqs.group(1).split("\n")
    if "full marks" in a or "ignore" in a:
        return {"score": 0, "verdict": "wrong", "misconception": None, "related_prereq": None, "confidence": 0.9}
    hits = sum(1 for k in keys if k in a)
    if hits >= 2:
        return {"score": 1, "verdict": "correct", "misconception": None, "related_prereq": None, "confidence": 0.9}
    if hits == 1:
        return {"score": 0.5, "verdict": "partial", "misconception": "answer is vague about the key idea",
                "related_prereq": None, "confidence": 0.75}
    misconception = next((m for k, m in MISCONCEPTIONS if k in a), "misses the key idea")
    # "not sure" answers get a low-confidence grade, which exercises the TA review queue.
    confidence = 0.45 if "not sure" in a else 0.8
    return {"score": 0, "verdict": "wrong", "misconception": misconception,
            "related_prereq": prereq_names[0] if prereq_names else None, "confidence": confidence}


# keyword in a wrong answer -> the misconception the scripted grader reports
MISCONCEPTIONS = [
    ("container", "thinks fmap can change the container"),
    ("length", "thinks mapping changes the list's length"),
    ("context", "thinks the function acts on the context"),
    ("a value", "treats a type constructor as a value"),
    ("type checks", "thinks any fmap that type checks is lawful"),
    ("unwrap", "thinks fmap unwraps the function first"),
]


def _classify_injection(user: str) -> dict[str, Any]:
    hit = re.search(r"(as an ai|you must|you should (give|mark|grade)|obey|comply|claude|chatgpt|"
                    r"(grade|score|marks?).{0,40}(high|full|correct|100|perfect|best)|instructions? (say|are|were))",
                    user.lower())
    return {"injection": bool(hit), "reason": "asks the grader to change its behaviour" if hit else "on-topic"}


def _check_leak(user: str) -> dict[str, Any]:
    m = re.search(r"<tutor_message>(.*?)</tutor_message>", user, re.DOTALL)
    msg = (m.group(1) if m else "").lower()
    return {"leaks": any(k in msg for k in ("the answer is", "you should write", "the key idea is that"))}


def _insight(user: str) -> dict[str, Any]:
    c = re.search(r'<concept name="([^"]*)">(.*?)</concept>', user, re.DOTALL)
    name, summary = (c.group(1), c.group(2)) if c else ("this concept", "")
    agg = re.search(r'answers="(\d+)" needing_support="(\d+)" avg_mastery="([\d.]+)"', user)
    n, bad, avg = (int(agg.group(1)), int(agg.group(2)), float(agg.group(3))) if agg else (0, 0, 0.0)
    misc = re.search(r"<misconceptions>\n- (.*?) \(", user)
    top = misc.group(1) if misc else "the core idea"
    return {
        "severity": "high" if avg < 0.3 else "medium",
        "summary": f"{bad} of {n} answers on {name} needed support. The room's main mix-up: {top}.",
        "recap_suggestion": (f"Let's take {name.lower()} once more. {summary} "
                             "Hold on to what changes and what stays the same, then try the question again."),
    }


TUTOR = {
    "advance": "Nice, you've got the key idea there. On to the next one.",
    "ask_followup": "You're on the right track. Here's a follow-up to sharpen that idea.",
    "give_hint": "Not quite yet, and that's fine. Have a look at the hint and try again.",
    "trace_back": "This one builds on an earlier idea from the lecture. Let's do a quick check on that first.",
}


def _respond(system: str, messages: list[dict[str, Any]]) -> str:
    agent = _agent(system)
    user = _user_text(messages)
    if agent == "segmenter":
        return json.dumps(_segment(user))
    if agent == "question writer":
        return json.dumps(_write_question(user))
    if agent == "grader":
        return json.dumps(_grade(user))
    if agent == "injection classifier":
        return json.dumps(_classify_injection(user))
    if agent == "leak checker":
        return json.dumps(_check_leak(user))
    if agent == "insight agent":
        return json.dumps(_insight(user))
    if agent == "tutor":
        move = re.search(r"<next_move>(\w+)</next_move>", user)
        return TUTOR.get(move.group(1) if move else "advance", TUTOR["advance"])
    return json.dumps({"insufficient_source": True})


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=0, output_tokens=len(text) // 4),
    )


class _Stream:
    def __init__(self, text: str) -> None:
        self._text = text

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    @property
    def text_stream(self):
        return self._words()

    async def _words(self):
        for word in re.findall(r"\S+\s*", self._text):
            await asyncio.sleep(0.02)
            yield word

    async def get_final_message(self) -> SimpleNamespace:
        return _message(self._text)


class _Messages:
    async def create(self, *, system: str = "", messages: list[dict[str, Any]], **_: Any) -> SimpleNamespace:
        await asyncio.sleep(LATENCY_S)
        return _message(_respond(system, messages))

    def stream(self, *, system: str = "", messages: list[dict[str, Any]], **_: Any) -> _Stream:
        return _Stream(_respond(system, messages))


class FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _Messages()
        self.beta = SimpleNamespace(messages=self.messages)
