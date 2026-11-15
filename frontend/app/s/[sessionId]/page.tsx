"use client";
// Student companion, built to the Figma frames "LectureLoop / Companion · Desktop" (xl, 3 columns)
// and "· Mobile" (stacked). Live data comes over the student WebSocket via useSession: concept
// timeline, grounded question + hint ladder, streamed feedback, trace-back, mastery and mode.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAvatarState, type AvatarState } from "@/components/companion/avatarState";
import { AvatarHalo } from "@/components/companion/AvatarHalo";
import { ProfCube } from "@/components/companion/ProfCube";
import { MasteryRing, ScoreTrend } from "@/components/companion/MasteryRing";
import { Pill, type PillTone } from "@/components/companion/Pill";
import { ConceptTimeline, upsertConcept } from "@/components/ConceptTimeline";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { InlineCode } from "@/components/InlineCode";
import { ModeBadge, modeBlurb } from "@/components/ModeBadge";
import {
  api,
  type Concept,
  type FeedbackOut,
  type HintOut,
  type Mode,
  type QuestionOut,
  type RecapOut,
  type RoomScoreOut,
  type SessionPublic,
  type StudentStatus,
  type TraceBack,
  type WSMessage,
} from "@/lib/api";
import { studentStore } from "@/lib/studentStore";
import { useSession, type ConnStatus } from "@/lib/useSession";

const VERDICT: Record<FeedbackOut["verdict"], { label: string; tone: PillTone }> = {
  correct: { label: "✓ correct", tone: "teal" },
  partial: { label: "◐ partly there", tone: "amber" },
  wrong: { label: "↺ not yet", tone: "periwinkle" },
};

type Feedback = { questionId: string; text: string; final?: FeedbackOut };
type Moment = { mastery: number; at: number }; // at = seconds into the lecture

const CARD = "rounded-[12px] border border-line bg-panel";
const LABEL = "font-code text-[10px] text-muted";

function mmss(t: number) {
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}
const pad2 = (n: number) => String(n).padStart(2, "0");

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "··";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default function StudentPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [studentId, setStudentId] = useState<string | null | undefined>(undefined);
  const [session, setSession] = useState<SessionPublic | null>(null);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [mastery, setMastery] = useState<Record<string, number>>({});
  const [mode, setMode] = useState<Mode>("normal");
  const [ready, setReady] = useState(true);
  const [nickname, setNickname] = useState("");
  const [moments, setMoments] = useState<Moment[]>([]);
  const [question, setQuestion] = useState<QuestionOut | null>(null);
  const [askedByConcept, setAskedByConcept] = useState<Record<string, string[]>>({});
  const [hints, setHints] = useState<HintOut[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [traceBack, setTraceBack] = useState<TraceBack | null>(null);
  const [recap, setRecap] = useState<RecapOut | null>(null);
  const [room, setRoom] = useState<RoomScoreOut | null>(null);
  const [answer, setAnswer] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const questionIdRef = useRef<string | null>(null);
  const answerRef = useRef<HTMLTextAreaElement>(null);
  const [avatar, setAvatar] = useAvatarState();
  // After a correct answer the question and its reflection stay put until the student presses Next;
  // a question that arrives meanwhile waits in `queuedRef`.
  const [awaitingNext, setAwaitingNext] = useState(false);
  const holdRef = useRef(false);
  const queuedRef = useRef<QuestionOut | null>(null);
  const [roomConceptId, setRoomConceptId] = useState<string | null>(null);

  const showQuestion = useCallback(
    (q: QuestionOut) => {
      if (questionIdRef.current !== q.question_id) {
        setHints([]);
        setAnswer("");
      }
      questionIdRef.current = q.question_id;
      setQuestion(q);
      setRoomConceptId(q.concept_id);
      setAvatar("idle");
      setAskedByConcept((a) => {
        const ids = a[q.concept_id] ?? [];
        return ids.includes(q.question_id) ? a : { ...a, [q.concept_id]: [...ids, q.question_id] };
      });
      setPending(false);
    },
    [setAvatar],
  );

  useEffect(() => setStudentId(studentStore.get(sessionId)), [sessionId]);
  useEffect(() => {
    api.getSession(sessionId).then(setSession, () => setSession(null));
  }, [sessionId]);

  const onMessage = useCallback((msg: WSMessage) => {
    const p = msg.payload as Record<string, unknown>;
    switch (msg.type) {
      case "status": {
        const s = p as unknown as StudentStatus;
        setConcepts(s.concepts);
        setMastery(s.mastery);
        setMode(s.mode);
        setReady(s.ready);
        setNickname(s.nickname);
        break;
      }
      case "concept_opened":
      case "concept_closed":
        setConcepts((cs) => upsertConcept(cs, p.concept as Concept));
        break;
      case "question": {
        const q = p as unknown as QuestionOut;
        if (holdRef.current && q.question_id !== questionIdRef.current) queuedRef.current = q;
        else showQuestion(q);
        break;
      }
      case "feedback_delta":
        setFeedback((f) =>
          f && f.questionId === p.question_id && !f.final
            ? { ...f, text: f.text + String(p.delta) }
            : { questionId: String(p.question_id), text: String(p.delta) },
        );
        break;
      case "feedback": {
        const fb = p as unknown as FeedbackOut & { question_id: string };
        setFeedback({ questionId: fb.question_id, text: fb.message, final: fb });
        setAvatar(fb.verdict === "correct" ? "happy" : "nudge");
        setMode(fb.mode);
        // A blocked answer was never graded, so it is not a "moment" on the trend line.
        if (!fb.blocked) setMoments((m) => [...m, { mastery: fb.mastery, at: fb.lecture_t }].slice(-20));
        if (fb.verdict === "correct" && !fb.blocked && fb.question_id === questionIdRef.current) {
          holdRef.current = true;
          setAwaitingNext(true);
        }
        setPending(false); // the server follows feedback with a "status" carrying per-concept mastery
        if (fb.next !== "trace_back") setTraceBack(null);
        break;
      }
      case "hint":
        setAvatar("explaining");
        setHints((h) => (h.some((x) => x.level === (p as HintOut).level) ? h : [...h, p as unknown as HintOut]));
        break;
      case "trace_back":
        setTraceBack(p as unknown as TraceBack);
        break;
      case "recap":
        setRecap(p as unknown as RecapOut);
        setAvatar("explaining");
        break;
      case "room_score":
        setRoom(p as unknown as RoomScoreOut);
        break;
      case "error":
        setError(String(p.message ?? "Something went wrong."));
        setPending(false);
        setAvatar("idle");
        break;
    }
  }, [setAvatar, showQuestion]);

  const { status, send } = useSession(studentId ? `/ws/student/${studentId}` : null, onMessage);

  function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!question || !answer.trim() || pending || awaitingNext) return;
    setError(null);
    if (send("answer", { question_id: question.question_id, text: answer.trim() })) {
      setPending(true);
      setFeedback(null);
      setAvatar("thinking");
    } else setError("Not connected. Retrying…");
  }

  function askHint() {
    if (!question) return;
    setError(null);
    send("hint_request", { question_id: question.question_id });
  }

  function toggleReady() {
    const next = !ready;
    setReady(next);
    send("set_ready", { ready: next });
  }

  function focusAnswer() {
    answerRef.current?.focus();
    answerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /** Next: leave the answered question. Shows the queued one, or the waiting state if none has arrived. */
  function next() {
    holdRef.current = false;
    setAwaitingNext(false);
    setFeedback(null);
    const q = queuedRef.current;
    queuedRef.current = null;
    if (q) {
      showQuestion(q);
      setTimeout(focusAnswer, 50);
    } else {
      questionIdRef.current = null;
      setQuestion(null);
      setHints([]);
      setAnswer("");
    }
  }

  if (studentId === null) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-6 p-8 text-center">
        <ProfCube state="idle" className="h-[180px] w-[160px]" />
        <p className="text-[15px]">You haven&apos;t joined this lecture on this device.</p>
        <Link
          href="/"
          className="rounded-[8px] bg-teal px-6 py-3 font-code text-[13px] font-bold text-ink hover:opacity-90"
        >
          Join with a code →
        </Link>
      </main>
    );
  }

  // ---- derived view state
  const hintsLeft = question ? question.hints_available - hints.length : 0;
  const conceptIndex = (id: string) => concepts.findIndex((c) => c.id === id);
  const closed = concepts.filter((c) => c.t_end != null);
  const scored = closed.filter((c) => mastery[c.id] !== undefined);
  const overall = scored.length ? scored.reduce((a, c) => a + mastery[c.id], 0) / scored.length : null;
  const lectureAt = concepts.reduce((t, c) => Math.max(t, c.t_end ?? c.t_start), 0);
  const ended = session?.status === "ended";
  const seatGone = status === "error";
  const companionState: { label: string; tone: PillTone } = !ready
    ? { label: "○ paused", tone: "muted" }
    : avatar === "thinking" || pending
      ? { label: "● thinking", tone: "amber" }
      : avatar === "happy"
        ? { label: "✓ nice one", tone: "teal" }
        : avatar === "nudge"
          ? { label: "◐ hmm…", tone: "amber" }
          : avatar === "explaining"
            ? { label: "✎ explaining", tone: "periwinkle" }
            : question
              ? { label: "● your turn", tone: "teal" }
              : { label: "● listening", tone: "teal" };

  return (
    <div className="mx-auto flex min-h-dvh max-w-[1440px] flex-col">
      <Header
        title={session?.title ?? "Joining lecture…"}
        nickname={nickname}
        lectureAt={concepts.length ? lectureAt : null}
        ended={ended}
        status={status}
      />

      {/* Learning thread + ready toggle (toggle sits inside this bar on desktop). */}
      <section
        aria-label="Your learning thread"
        className="border-b border-line px-[19px] pb-[14px] pt-[15px] xl:flex xl:min-h-[108px] xl:items-end xl:gap-6 xl:px-[31px] xl:pb-[25px] xl:pt-[17px]"
      >
        <div className="min-w-0 flex-1">
          <p className={LABEL}>{"// YOUR LEARNING THREAD"}</p>
          <div className="mt-[13px] xl:mt-[17px]">
            <ConceptTimeline concepts={concepts} currentId={question?.concept_id} />
          </div>
        </div>
        <div className="hidden xl:mb-[5px] xl:block xl:pr-[56px]">
          <ReadyToggle ready={ready} onToggle={toggleReady} />
        </div>
      </section>
      <div className="px-5 pt-5 xl:hidden">
        <ReadyToggle ready={ready} onToggle={toggleReady} />
      </div>

      <main className="flex flex-1 flex-col gap-4 px-5 pt-[18px] xl:grid xl:grid-cols-[442fr_620fr_266fr] xl:items-start xl:gap-6 xl:px-8 xl:pt-7">
        {/* Column 1: companion + trace-back. `contents` lets mobile re-order cards across columns. */}
        <div className="contents xl:flex xl:flex-col xl:gap-5">
          <CompanionStage
            className="order-1 xl:order-none"
            state={companionState}
            avatar={avatar}
            line={companionLine({ ready, pending, question, traceBack, mode, closed: closed.length, ended, seatGone, avatar })}
            sub={ready ? "I’ll wait until you’re ready." : "Questions paused. Flip the switch when you want one."}
          />
          {recap && (
            <section data-testid="recap" className={`${CARD} order-1 border-amber/60 p-[19px] pt-5 xl:order-none xl:p-[21px]`}>
              <div className="flex items-start justify-between gap-4">
                <p className="whitespace-pre font-code text-[10px] text-amber xl:text-[11px]">
                  {"✎  FROM YOUR LECTURER"}
                </p>
                <button
                  type="button"
                  onClick={() => setRecap(null)}
                  aria-label="Dismiss recap"
                  className="-mt-1 font-code text-[14px] text-muted hover:text-fg"
                >
                  ×
                </button>
              </div>
              <p className="mt-3 font-code text-[10px] lowercase text-muted">{`// recap · ${recap.concept_name}`}</p>
              <p className="mt-3 text-[14px]">
                <InlineCode text={recap.text} />
              </p>
            </section>
          )}
          {traceBack && (
            <section data-testid="trace-back" className={`${CARD} order-6 p-[19px] pt-5 xl:order-none xl:p-[21px]`}>
              <p className="whitespace-pre font-code text-[10px] text-periwinkle xl:text-[11px]">{"↩  TRACE-BACK"}</p>
              <p className="mt-5 text-[14px] xl:mt-[18px]">
                This builds on ‘{traceBack.to_concept}’ from [{traceBack.at}]. Quick check?
              </p>
              <button
                type="button"
                onClick={focusAnswer}
                disabled={!question}
                className="mt-4 flex h-10 w-full items-center justify-center whitespace-pre rounded-[8px] border border-line font-code text-[11px] text-periwinkle transition hover:bg-raised disabled:opacity-50 xl:mt-[14px] xl:h-[43px] xl:w-[218px] xl:-translate-x-2 xl:justify-start xl:px-2 xl:text-[12px]"
              >
                {"Revisit the idea  →"}
              </button>
            </section>
          )}
        </div>

        {/* Column 2: question, reflection, progress. */}
        <div className="contents xl:flex xl:flex-col xl:gap-5">
          {seatGone ? (
            <section className={`${CARD} order-2 p-[19px] xl:order-none xl:p-[27px]`}>
              <p className="font-code text-[11px] text-amber">{"// seat not found"}</p>
              <p className="mt-3 text-[15px]">This seat no longer exists on the server.</p>
              <Link href="/" className="mt-4 inline-block font-code text-[12px] text-teal hover:underline">
                Join again →
              </Link>
            </section>
          ) : question ? (
            <QuestionCard
              className="order-2 xl:order-none"
              question={question}
              questionNo={(askedByConcept[question.concept_id]?.indexOf(question.question_id) ?? 0) + 1}
              conceptNo={conceptIndex(question.concept_id) + 1}
              locked={awaitingNext}
              hints={hints}
              hintsLeft={hintsLeft}
              answer={answer}
              setAnswer={setAnswer}
              answerRef={answerRef}
              pending={pending}
              connected={status === "connected"}
              onSubmit={submit}
              onHint={askHint}
            />
          ) : (
            <section className={`${CARD} order-2 p-[19px] pt-[21px] xl:order-none xl:min-h-[240px] xl:p-[27px] xl:pt-[23px]`}>
              <p className="font-code text-[10px] text-teal xl:text-[11px]">
                {ended ? "// lecture ended" : "// waiting for the next concept"}
              </p>
              <p className="mt-6 max-w-[564px] text-[22px] font-medium xl:text-[26px]">
                {ended
                  ? "That’s the lecture. Nice work keeping up."
                  : ready
                    ? "Listening to the lecture."
                    : "Questions are paused."}
              </p>
              <p className="mt-3 text-[14px] text-muted">
                {ended
                  ? "Your mastery is saved for this session."
                  : ready
                    ? "Your next question appears when a concept wraps up."
                    : "Switch on “Ready for a question” when you want one."}
              </p>
            </section>
          )}

          {(feedback || pending) && (
            <FeedbackCard className="order-3 xl:order-none" feedback={feedback} onNext={awaitingNext ? next : undefined} />
          )}

          {closed.length > 0 && (
            <ProgressCard
              className="hidden xl:flex"
              done={scored.length}
              total={closed.length}
              canJump={awaitingNext || (!!question && !pending)}
              onNext={awaitingNext ? next : focusAnswer}
            />
          )}

          {error && (
            <p role="alert" className={`${CARD} order-4 px-5 py-3 font-code text-[11px] text-amber xl:order-none`}>
              {error}
            </p>
          )}
        </div>

        {/* Column 3: confidence + privacy note. */}
        <div className="contents xl:flex xl:flex-col xl:gap-5">
          <ConfidencePanel className="order-5 xl:order-none" overall={overall}
            moments={moments}
            mode={mode}
            room={room}
            roomConcept={concepts.find((c) => c.id === (question?.concept_id ?? roomConceptId))}
          />
          <section className="hidden rounded-[12px] border border-line p-[21px] pt-[23px] xl:block">
            <p className={LABEL}>JUST BETWEEN US</p>
            <p className="mt-[19px] max-w-[222px] text-[13px] text-muted">
              Your answers are private. Your lecturer sees the room, not your response.
            </p>
            <p className="mt-5 font-code text-[10px] text-periwinkle">Learn without the spotlight.</p>
          </section>
        </div>
      </main>

      <footer className="flex flex-col gap-[60px] px-5 pb-10 pt-[33px] xl:flex-row xl:items-center xl:justify-between xl:px-8 xl:pb-8 xl:pt-[35px]">
        <p className="font-code text-[11px] text-muted xl:hidden">Your answers stay between us.</p>
        <ConnectionBadge status={status} />
        <p className="hidden font-code text-[10px] text-muted xl:block">Your pace, not a race.</p>
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------- pieces

function Header({
  title,
  nickname,
  lectureAt,
  ended,
  status,
}: {
  title: string;
  nickname: string;
  lectureAt: number | null;
  ended: boolean;
  status: ConnStatus;
}) {
  const live = ended ? (
    <Pill tone="muted">○ ended</Pill>
  ) : status === "connected" ? (
    <Pill tone="teal" className="w-[78px] xl:w-20">
      ● live
    </Pill>
  ) : (
    <Pill tone="amber">○ offline</Pill>
  );
  const at = `[ ${lectureAt === null ? "--:--" : mmss(lectureAt)} ]`;
  return (
    <header className="border-b border-line px-[19px] pb-[9px] pt-4 xl:flex xl:h-[88px] xl:items-center xl:px-[31px] xl:py-0">
      <div className="flex items-center justify-between xl:contents">
        <p className="font-code text-[16px] font-medium xl:flex xl:items-center xl:gap-[18px] xl:text-[18px]">
          <span className="text-teal xl:text-[30px] xl:leading-none">↻</span> LectureLoop
        </p>
        <div className="xl:hidden">{live}</div>
      </div>
      <span aria-hidden className="mx-6 hidden h-8 w-px bg-line xl:ml-[56px] xl:block" />
      <h1 className="mt-[14px] text-[13px] font-medium xl:mt-0 xl:text-[14px]">{title}</h1>
      <p className="mt-[6px] whitespace-pre font-code text-[10px] text-muted xl:hidden">
        {at.replaceAll(" ", "")}
        {"  /  live companion"}
      </p>
      <div className="ml-auto hidden items-center gap-5 xl:flex">
        {live}
        <span className="font-code text-[12px] text-muted">{at}</span>
        <span
          title={nickname || undefined}
          className="ml-[150px] mr-[41px] inline-flex h-[34px] w-12 items-center justify-center rounded-full border border-muted bg-panel font-code text-[11px] text-muted"
        >
          {initials(nickname)}
        </span>
      </div>
    </header>
  );
}

function ReadyToggle({ ready, onToggle }: { ready: boolean; onToggle: () => void }) {
  return (
    <div className="flex items-center justify-between gap-[60px]">
      <span id="ready-label" className="font-code text-[11px]">
        Ready for a question
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={ready}
        aria-labelledby="ready-label"
        onClick={onToggle}
        className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${ready ? "bg-teal" : "bg-line"}`}
      >
        <span
          className={`absolute top-[3px] size-[18px] rounded-[9px] transition-all ${
            ready ? "left-[23px] bg-ink" : "left-[3px] bg-muted"
          }`}
        />
      </button>
    </div>
  );
}

function companionLine(s: {
  ready: boolean;
  pending: boolean;
  question: QuestionOut | null;
  traceBack: TraceBack | null;
  mode: Mode;
  closed: number;
  ended: boolean;
  seatGone: boolean;
  avatar: AvatarState;
}): string {
  if (s.seatGone) return "I can’t find your seat. Rejoin and I’ll pick up where we left off.";
  if (s.ended && !s.question) return "That’s a wrap. Good thinking today.";
  if (!s.ready) return "Taking a breather. I’m still listening.";
  if (s.pending) return "Reading your answer. One moment.";
  if (s.avatar === "happy") return "Yes! That’s exactly the idea.";
  if (s.avatar === "nudge") return "Hmm, not quite yet. Let’s look at it again.";
  if (s.avatar === "explaining") return "Here’s a pointer. Chalk at the ready.";
  if (s.question && s.traceBack) return `Same structure, earlier idea. Let’s revisit ‘${s.traceBack.to_concept}’.`;
  if (s.question) {
    if (s.mode === "support") return "One step at a time. Hints are there if you want them.";
    if (s.mode === "challenge") return "You’re flying. Here’s a harder one.";
    return "Take your time. Say it in your own words.";
  }
  return s.closed ? "Listening along. Your next question lands when this topic wraps up." : "Listening along with you. Questions arrive as each idea wraps up.";
}

function CompanionStage({
  state,
  avatar,
  line,
  sub,
  className = "",
}: {
  state: { label: string; tone: PillTone };
  avatar: AvatarState;
  line: string;
  sub: string;
  className?: string;
}) {
  return (
    <section aria-label="Your companion" className={`${CARD} ${className}`}>
      {/* Mobile: avatar left, speech right. */}
      <div className="flex min-h-[170px] items-start pl-[13px] pr-[19px] pt-[22px] xl:hidden">
        <ProfCube state={avatar} className="h-[130px] w-[110px] shrink-0" />
        <div className="ml-[22px] mt-[7px] min-w-0 flex-1">
          <p aria-live="polite" className="min-h-[94px] rounded-[12px] border border-line bg-raised px-[13px] py-[15px] text-[13px]">
            {line}
          </p>
          <p className={`ml-1 mt-[15px] font-code text-[9px] uppercase ${state.tone === "amber" ? "text-amber" : state.tone === "muted" ? "text-muted" : "text-teal"}`}>
            {state.label}
          </p>
        </div>
      </div>

      {/* Desktop: tall stage with halo. */}
      <div className="hidden min-h-[596px] flex-col items-center px-[23px] pb-10 pt-[15px] xl:flex">
        <div className="flex w-full items-center justify-between">
          <p className="font-code text-[11px] text-muted">{"// YOUR COMPANION"}</p>
          <Pill tone={state.tone} className="min-w-[132px]">
            {state.label}
          </Pill>
        </div>
        <p
          aria-live="polite"
          className="mt-12 min-h-20 w-full max-w-[366px] rounded-[12px] border border-line bg-raised p-[19px] text-[15px]"
        >
          {line}
        </p>
        <div className="relative mt-4">
          <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <AvatarHalo />
          </div>
          <ProfCube state={avatar} className="h-[300px] w-[300px]" />
        </div>
        <p className="relative mt-4 font-code text-[11px] text-muted">PROF. CUBE / STUDY PARTNER</p>
        <p className="relative mt-[21px] text-[13px] text-muted">{sub}</p>
      </div>
    </section>
  );
}

function QuestionCard({
  question,
  questionNo,
  conceptNo,
  locked,
  hints,
  hintsLeft,
  answer,
  setAnswer,
  answerRef,
  pending,
  connected,
  onSubmit,
  onHint,
  className = "",
}: {
  question: QuestionOut;
  questionNo: number;
  conceptNo: number;
  locked: boolean; // answered correctly; waiting for the student to press Next
  hints: HintOut[];
  hintsLeft: number;
  answer: string;
  setAnswer: (s: string) => void;
  answerRef: React.RefObject<HTMLTextAreaElement | null>;
  pending: boolean;
  connected: boolean;
  onSubmit: (e?: React.FormEvent) => void;
  onHint: () => void;
  className?: string;
}) {
  const level = Math.max(1, Math.min(3, question.difficulty));
  return (
    <section aria-label="Question" className={`${CARD} px-[19px] pb-6 pt-[21px] xl:px-[27px] xl:pb-[36px] xl:pt-[23px] ${className}`}>
      <div className="flex items-center justify-between">
        <p className="font-code text-[10px] text-teal xl:text-[11px]">
          {`// question ${questionNo} of concept ${pad2(Math.max(conceptNo, 1))}`}
        </p>
        <p
          aria-label={`Difficulty ${level} of 3`}
          className="whitespace-pre font-code text-[10px] text-amber xl:mr-[17px] xl:text-[12px]"
        >
          {[1, 2, 3].map((i) => (i <= level ? "●" : "○")).join(" ")}
        </p>
      </div>
      <h2 data-testid="question-text" className="mt-[23px] max-w-[564px] text-[25px] font-medium xl:mt-[25px] xl:text-[26px]">
        <InlineCode text={question.text} />
      </h2>

      <figure className="relative mt-[26px] rounded-[6px] bg-ink px-3 py-[17px] font-code text-[10px] text-muted xl:mt-[25px] xl:px-4 xl:text-[11px]">
        <span aria-hidden className="absolute inset-y-0 left-0 hidden w-[2px] bg-line xl:block" />
        <figcaption className="sr-only">From the lecture</figcaption>
        <blockquote className="flex gap-[1ch] xl:gap-[2ch]">
          <span className="shrink-0">[{question.source_t == null ? "slides" : mmss(question.source_t)}]</span>
          <span className="-indent-[3ch] pl-[3ch]">{`// “${question.source_quote}”`}</span>
        </blockquote>
      </figure>

      {hints.length > 0 && (
        <ol className="mt-4 space-y-2">
          {hints.map((h) => (
            <li key={h.level} className="rounded-[8px] border border-line bg-raised px-4 py-3 text-[14px]">
              <span className="mr-2 font-code text-[10px] text-periwinkle">HINT {h.level}</span>
              <InlineCode text={h.text} />
            </li>
          ))}
        </ol>
      )}

      <form onSubmit={onSubmit}>
        <label htmlFor="answer" className={`mt-6 block ${LABEL}`}>
          YOUR THINKING
        </label>
        <textarea
          id="answer"
          ref={answerRef}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSubmit();
            }
          }}
          maxLength={500}
          readOnly={locked}
          placeholder="In your own words…"
          className="mt-[11px] block h-[116px] w-full resize-none rounded-[8px] border border-line bg-ink px-3 py-4 text-[14px] text-fg outline-none transition-colors placeholder:text-muted/60 focus:border-teal xl:px-[15px] xl:text-[15px]"
        />
        <div className="mt-6 flex items-center gap-3 xl:mt-[28px] xl:gap-4">
          <button
            disabled={pending || locked || !answer.trim() || !connected}
            className="h-12 flex-1 rounded-[8px] bg-teal font-code text-[12px] font-bold text-ink transition hover:opacity-90 disabled:opacity-50 xl:w-[168px] xl:flex-none xl:text-[13px]"
          >
            {pending ? "Checking…" : locked ? "Answered ✓" : "Submit ⏎"}
          </button>
          <button
            type="button"
            onClick={onHint}
            disabled={hintsLeft <= 0 || pending || locked || !connected}
            className="h-12 flex-1 rounded-[8px] border border-line font-code text-[11px] text-muted transition hover:bg-raised disabled:opacity-50 xl:w-[158px] xl:flex-none"
          >
            Hint ({hintsLeft} left)
          </button>
          <p className="ml-auto hidden font-code text-[10px] text-muted xl:mr-[22px] xl:block">
            Shift + Enter
            <br />
            for a new line
          </p>
        </div>
      </form>
    </section>
  );
}

function FeedbackCard({
  feedback,
  onNext,
  className = "",
}: {
  feedback: Feedback | null;
  onNext?: () => void; // set after a correct answer: the card stays until the student moves on
  className?: string;
}) {
  const f = feedback?.final;
  const v: { label: string; tone: PillTone } | null = !f
    ? null
    : f.blocked
      ? { label: "⛨ blocked", tone: "coral" }
      : f.reviewed
        ? { label: `✓ TA: ${f.verdict}`, tone: f.verdict === "correct" ? "teal" : "amber" }
        : VERDICT[f.verdict];
  return (
    <section
      data-testid="feedback"
      aria-live="polite"
      className={`${CARD} relative min-h-[168px] px-[19px] pb-6 pt-[17px] xl:min-h-[144px] xl:px-[23px] xl:pt-5 ${className}`}
    >
      <div className="flex items-center gap-3">
        {v ? (
          <Pill tone={v.tone} data-blocked={f?.blocked || undefined} className="min-w-[106px] xl:min-w-[110px]">
            {v.label}
          </Pill>
        ) : (
          <Pill tone="amber" className="min-w-[106px] animate-pulse xl:min-w-[110px]">
            ● reading
          </Pill>
        )}
        <p className="absolute left-[229px] top-[27px] font-code text-[9px] text-muted xl:hidden">{"// REFLECTION"}</p>
        <p className="absolute left-[341px] top-[31px] hidden font-code text-[10px] text-muted xl:block">
          {"// PROF. CUBE’S REFLECTION"}
        </p>
      </div>
      <p className="mt-[22px] text-[14px] xl:mt-5">
        <InlineCode text={feedback?.text || "Prof. Cube is reading your answer…"} />
        {!feedback?.final && <span className="ml-1 animate-pulse">▋</span>}
      </p>
      {onNext && (
        <button
          type="button"
          data-testid="next"
          onClick={onNext}
          className="mt-5 h-10 rounded-[8px] border border-teal px-5 font-code text-[12px] text-teal transition hover:bg-teal-deep"
        >
          Next →
        </button>
      )}
    </section>
  );
}

function ProgressCard({
  done,
  total,
  canJump,
  onNext,
  className = "",
}: {
  done: number;
  total: number;
  canJump: boolean;
  onNext: () => void;
  className?: string;
}) {
  const left = Math.max(0, total - done);
  return (
    <section className={`min-h-[95px] items-center rounded-[12px] border border-line px-[23px] py-5 ${className}`}>
      <div>
        <p className="font-code text-[11px] text-teal">
          {pad2(Math.min(done + (canJump ? 1 : 0), total))} / {pad2(total)}
        </p>
        <p className="mt-3 text-[13px] text-muted">
          {left === 0
            ? "You’re all caught up. The next question lands when the lecture moves on."
            : left === 1
              ? "One more connection, then you’re all caught up."
              : `${left} more connections, then you’re all caught up.`}
        </p>
      </div>
      {canJump && (
        <button type="button" onClick={onNext} className="ml-auto mr-[23px] font-code text-[12px] text-teal hover:underline">
          Next →
        </button>
      )}
    </section>
  );
}

/** Class challenge: one score for the whole room. Never a ranking, never another student's name. */
function RoomLine({ room, concept }: { room: RoomScoreOut | null; concept?: Concept }) {
  if (!room || room.score == null) return null;
  const onConcept = concept ? room.concepts[concept.id] : undefined;
  return (
    <p data-testid="room-score" className="font-code text-[10px] text-muted">
      THE ROOM · <span className="text-teal">{Math.round(room.score * 100)}%</span>
      {concept && onConcept !== undefined && (
        <span className="lowercase">{` · ${Math.round(onConcept * 100)}% on ${concept.name}`}</span>
      )}
    </p>
  );
}

function ConfidencePanel({
  overall,
  moments,
  mode,
  room,
  roomConcept,
  className = "",
}: {
  overall: number | null;
  moments: Moment[];
  mode: Mode;
  room: RoomScoreOut | null;
  roomConcept?: Concept;
  className?: string;
}) {
  const pct = overall === null ? "—" : `${Math.round(overall * 100)}%`;
  const values = moments.map((m) => m.mastery);
  const [b1, b2] = modeBlurb(mode);
  return (
    <section aria-label="Confidence" className={`${CARD} ${className}`}>
      {/* Mobile: compact row. */}
      <div className="min-h-[207px] p-[19px] xl:hidden">
        <p className={LABEL}>{"// CONFIDENCE"}</p>
        <div className="mt-[30px] flex gap-6">
          <div className="relative shrink-0">
            <MasteryRing value={overall} size={86} />
            <span className="absolute inset-0 flex items-center justify-center font-code text-[20px] font-medium">{pct}</span>
          </div>
          <div className="mt-[6px] min-w-0 flex-1">
            <p className="font-code text-[11px] text-muted">overall mastery</p>
            <div className="mt-[15px]">
              <ModeBadge mode={mode} />
            </div>
          </div>
        </div>
        <div className="mt-1 flex items-end justify-between gap-4">
          <p className="font-code text-[9px] text-muted">RECENT SCORES</p>
          <ScoreTrend values={values} className="h-[37px] w-[187px] max-w-[60%]" />
        </div>
        <div className="mt-4">
          <RoomLine room={room} concept={roomConcept} />
        </div>
      </div>

      {/* Desktop: centred ring. */}
      <div className="hidden min-h-[504px] p-[21px] pt-[23px] xl:block">
        <p className="font-code text-[11px] text-muted">{"// CONFIDENCE"}</p>
        <div className="relative mx-auto mt-[38px] size-[154px]">
          <MasteryRing value={overall} size={154} />
          <div className="absolute inset-0 flex flex-col items-center justify-center pt-1">
            <span className="font-code text-[31px] font-medium leading-none">{pct}</span>
            <span className="mt-3 font-code text-[10px] text-muted">overall mastery</span>
          </div>
        </div>
        <p className={`mt-[38px] ${LABEL}`}>RECENT MOMENTS</p>
        <div className="relative mt-5 h-11">
          <span aria-hidden className="absolute inset-x-0 top-[34px] h-px bg-line" />
          <ScoreTrend values={values} className="relative h-11 w-full" />
        </div>
        <div className="mt-[5px] flex justify-between font-code text-[9px] text-muted">
          <span>{moments.length ? mmss(moments[0].at) : ""}</span>
          <span>now</span>
        </div>
        <div className="mt-[35px]">
          <ModeBadge mode={mode} />
        </div>
        <p className="mt-[22px] text-[12px] text-muted">
          {b1}
          <br />
          {b2}
        </p>
        <div className="mt-6 border-t border-line pt-4">
          <RoomLine room={room} concept={roomConcept} />
        </div>
      </div>
    </section>
  );
}
