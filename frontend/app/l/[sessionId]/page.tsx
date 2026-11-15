"use client";
// Lecturer dashboard, built to the Figma frame "LectureLoop / Lecturer · Dashboard". Aggregates
// only: per-concept mastery tiles, the room insight, common misconceptions, room pulse and a
// teaching cue for the weakest concept, where the lecturer approves, edits or dismisses the suggested
// recap (human in the loop). Demo replay, live microphone mode and slide upload live in the footer.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Pill } from "@/components/companion/Pill";
import { upsertConcept } from "@/components/ConceptTimeline";
import {
  api,
  ApiError,
  type Concept,
  type Escalation,
  type HeatmapConcept,
  type ModeCounts,
  type ReplayStatus,
  type SessionPublic,
  type WSMessage,
} from "@/lib/api";
import { useLiveTranscript } from "@/lib/useLiveTranscript";
import { useSession, type ConnStatus } from "@/lib/useSession";

const CARD = "rounded-[12px] border border-line bg-panel";
const SPEEDS = [1, 2, 4];

function mmss(t: number) {
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}
const pad2 = (n: number) => String(n).padStart(2, "0");

type Band = "strong" | "mixed" | "weak" | "none" | "open";
const BAND: Record<Band, { text: string; bar: string; label: string; tile: string }> = {
  strong: { text: "text-teal", bar: "bg-teal", label: "well understood", tile: "border-line bg-panel" },
  mixed: { text: "text-amber", bar: "bg-amber", label: "developing", tile: "border-line bg-panel" },
  weak: { text: "text-coral", bar: "bg-coral", label: "↓ needs a pause", tile: "border-coral bg-coral-deep" },
  none: { text: "text-muted", bar: "bg-muted", label: "waiting for answers", tile: "border-line bg-panel" },
  open: { text: "text-muted", bar: "bg-muted", label: "being taught now", tile: "border-line bg-panel" },
};
function band(c: Concept, h: HeatmapConcept | undefined): Band {
  if (c.t_end == null) return "open";
  if (!h || h.n_active === 0) return "none";
  if (h.avg_mastery >= 0.7) return "strong";
  if (h.avg_mastery >= 0.45) return "mixed";
  return "weak";
}

export default function LecturerPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<SessionPublic | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [counts, setCounts] = useState({ n_students: 0, n_online: 0 });
  const [modes, setModes] = useState<ModeCounts>({ normal: 0, support: 0, challenge: 0 });
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [heat, setHeat] = useState<Record<string, HeatmapConcept>>({});
  const [heatAt, setHeatAt] = useState<number | null>(null);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const [speed, setSpeed] = useState(2);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [roomScore, setRoomScore] = useState<number | null>(null);
  const [slides, setSlides] = useState<string | null>(null);
  const live = useLiveTranscript(sessionId);
  const [, setTick] = useState(0);

  useEffect(() => {
    api.getSession(sessionId).then(setSession, (e) => setLoadError(e instanceof ApiError ? e.message : String(e)));
  }, [sessionId]);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 5000); // refresh "Updated Ns ago"
    return () => clearInterval(t);
  }, []);

  const onMessage = useCallback((msg: WSMessage) => {
    const p = msg.payload as Record<string, unknown>;
    switch (msg.type) {
      case "heatmap": {
        const h = p as {
          concepts: HeatmapConcept[];
          n_students: number;
          n_online: number;
          modes?: ModeCounts;
          room_score?: number | null;
        };
        setRoomScore(h.room_score ?? null);
        setHeat(Object.fromEntries(h.concepts.map((c) => [c.id, c])));
        setCounts({ n_students: h.n_students, n_online: h.n_online });
        if (h.modes) setModes(h.modes);
        setHeatAt(Date.now());
        break;
      }
      case "concept_opened":
      case "concept_closed":
        setConcepts((cs) => upsertConcept(cs, p.concept as Concept));
        break;
      case "escalation": {
        const e = p.escalation as Escalation;
        setEscalations((es) => (es.some((x) => x.id === e.id) ? es.map((x) => (x.id === e.id ? e : x)) : [e, ...es]));
        break;
      }
      case "replay_status": {
        const r = p as unknown as ReplayStatus;
        setReplay(r);
        if (r.running) setSpeed(r.speed); // keep the speed buttons honest if another tab changed it
        break;
      }
    }
  }, []);
  const { status } = useSession(session ? `/ws/lecturer/${sessionId}` : null, onMessage);

  async function run<T>(fn: () => Promise<T>) {
    setActionError(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "running" in r) setReplay(r as unknown as ReplayStatus);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    }
  }

  function changeSpeed(v: number) {
    setSpeed(v);
    if (replay?.running) run(() => api.setReplaySpeed(sessionId, v));
  }

  if (loadError) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-10">
        <p className="font-code text-[13px] text-coral">{loadError}</p>
      </main>
    );
  }
  if (!session) {
    return (
      <main className="mx-auto max-w-[1440px] p-10">
        <div className="h-8 w-64 animate-pulse rounded bg-panel" />
        <div className="mt-6 h-64 animate-pulse rounded-[12px] bg-panel" />
      </main>
    );
  }

  // ---- derived view state
  const ended = session.status === "ended";
  const bands = concepts.map((c) => ({ c, h: heat[c.id], b: band(c, heat[c.id]) }));
  const weakest = bands
    .filter((x) => x.b === "weak")
    .sort((a, b) => (a.h?.avg_mastery ?? 1) - (b.h?.avg_mastery ?? 1))[0];
  // The concept the lower cards talk about: the weakest one, else the latest with answers.
  const focus = weakest ?? [...bands].reverse().find((x) => (x.h?.n_answers ?? 0) > 0) ?? null;
  // The teaching cue follows the newest weak-concept alert (open first), else the weakest concept.
  const alerts = escalations.filter((e) => e.type === "weak_concept");
  const alert = alerts.find((e) => e.status === "open") ?? alerts[0];
  const cueConcept = alert ? concepts.find((c) => c.id === alert.payload.concept_id) : weakest?.c;
  const toReview = escalations.filter((e) => e.type !== "weak_concept" && e.status === "open").length;

  async function uploadSlides(file: File) {
    setActionError(null);
    try {
      const r = await api.uploadSlides(sessionId, file);
      setSlides(`${r.pages} slide${r.pages === 1 ? "" : "s"} loaded`);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-[1440px] flex-col">
      <Header
        session={session}
        nOnline={counts.n_online}
        nStudents={counts.n_students}
        roomScore={roomScore}
        at={live.live || live.elapsed > 0 ? live.elapsed : (replay?.position_s ?? 0)}
        onEnd={() => run(async () => setSession(await api.endSession(sessionId)))}
      />

      <main className="grid flex-1 gap-6 px-5 pt-8 sm:px-10 xl:grid-cols-[924fr_400fr] xl:gap-9 xl:pt-[37px]">
        <div className="flex min-w-0 flex-col gap-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="text-[25px] font-medium">The room, at a glance.</h2>
              <p className="mt-[10px] text-[15px] text-muted">Live understanding, not just attendance.</p>
            </div>
            <ul className="flex gap-6 pb-[1px] font-code text-[11px] xl:mr-[80px]" aria-label="Legend">
              <li className="text-teal">● strong</li>
              <li className="text-amber">● mixed</li>
              <li className="text-coral">● needs a pause</li>
            </ul>
          </div>

          {concepts.length === 0 ? (
            <p className="rounded-[12px] border border-dashed border-line p-8 text-[15px] text-muted">
              No concepts yet. Start the replay below and they appear here as the lecture moves through topics.
            </p>
          ) : (
            <ol className="grid grid-cols-2 gap-4 md:grid-cols-4">
              {bands.map(({ c, h, b }, i) => (
                <MasteryTile key={c.id} no={i + 1} concept={c} heat={h} band={b} />
              ))}
            </ol>
          )}

          <RoomInsight weakest={weakest?.h} anyAnswers={bands.some((x) => (x.h?.n_answers ?? 0) > 0)} />

          <div className="grid gap-6 md:grid-cols-[562fr_338fr]">
            <Misconceptions heat={focus?.h} conceptName={focus?.c.name} />
            <RoomPulse answers={focus?.h?.n_answers ?? 0} modes={modes} heatAt={heatAt} />
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <TeachingCue
            alert={alert}
            concept={cueConcept}
            heat={cueConcept ? heat[cueConcept.id] : undefined}
            no={cueConcept ? concepts.indexOf(cueConcept) + 1 : 0}
            onResolve={(id, action, edit) => run(() => api.resolveEscalation(id, action, { edit }))}
          />
          <section className="rounded-[12px] border border-line p-[23px] pt-[22px] xl:min-h-[176px]">
            <p className="font-code text-[11px] text-periwinkle">{"// SIGNAL, NOT SURVEILLANCE"}</p>
            <p className="mt-[19px] max-w-[352px] text-[15px] text-muted">
              You see patterns, not individual answers. Give the whole room permission to take a moment.
            </p>
          </section>
        </div>
      </main>

      <Footer
        status={status}
        ended={ended}
        replay={replay}
        speed={speed}
        onSpeed={changeSpeed}
        onStart={() => run(() => api.startReplay(sessionId, speed))}
        onPause={() => run(() => api.pauseReplay(sessionId))}
        error={actionError ?? live.error}
        live={live}
        slides={slides}
        onSlides={uploadSlides}
        opsHref={`/l/${sessionId}/ops`}
        toReview={toReview}
      />
    </div>
  );
}

// ---------------------------------------------------------------- pieces

function Header({
  session,
  nOnline,
  nStudents,
  roomScore,
  at,
  onEnd,
}: {
  session: SessionPublic;
  nOnline: number;
  nStudents: number;
  roomScore: number | null;
  at: number;
  onEnd: () => void;
}) {
  const ended = session.status === "ended";
  return (
    <header className="flex flex-wrap items-start gap-x-10 gap-y-6 border-b border-line px-5 py-6 sm:px-10 xl:h-[162px] xl:flex-nowrap xl:py-0">
      <div className="min-w-0 xl:w-[520px] xl:pt-6">
        <p className="flex items-baseline gap-[22px]">
          <span className="font-code text-[17px] font-medium">
            <span className="text-teal">↻</span> LectureLoop
          </span>
          <span className="font-code text-[11px] text-muted">/ LECTURER</span>
        </p>
        <h1 className="mt-5 break-words text-[32px] xl:truncate font-medium">{session.title}</h1>
        <p className="mt-[10px] whitespace-pre font-code text-[13px] text-muted">
          {`${nStudents} joined  /  ${ended ? "lecture ended" : "lecture live"}`}
          {roomScore !== null && (
            <span data-testid="room-score">
              {"  /  room "}
              <span className="text-teal">{Math.round(roomScore * 100)}%</span>
            </span>
          )}
        </p>
      </div>

      <div className="flex items-center gap-[23px] xl:pt-[31px]">
        <Pill tone={ended ? "muted" : "teal"} data-testid="n-online" className="xl:min-w-[224px]">
          {ended ? "○ session ended" : `● ${nOnline} student${nOnline === 1 ? "" : "s"} active`}
        </Pill>
        <span className="font-code text-[13px] text-muted">[{mmss(at)}]</span>
      </div>

      <div className="flex items-end gap-10 xl:ml-auto xl:pr-[1px] xl:pt-7">
        <div>
          <p className="ml-[3px] font-code text-[11px] text-muted">JOIN CODE</p>
          <p data-testid="join-code" className="mt-[11px] font-code text-[38px] font-medium leading-none text-teal">
            {session.join_code}
          </p>
        </div>
        <button
          onClick={onEnd}
          disabled={ended}
          className="mb-[-2px] h-9 w-[156px] rounded-[8px] border border-line font-code text-[12px] text-muted transition hover:bg-panel disabled:opacity-50 xl:mb-[-10px]"
        >
          {ended ? "Session ended" : "End session"}
        </button>
      </div>
    </header>
  );
}

function MasteryTile({ no, concept, heat, band: b }: { no: number; concept: Concept; heat?: HeatmapConcept; band: Band }) {
  const s = BAND[b];
  const pct = b === "open" || b === "none" || !heat ? null : Math.round(heat.avg_mastery * 100);
  const labelTone = b === "open" || b === "none" ? "text-teal" : s.text;
  return (
    <li
      title={heat?.top_misconception ? `Most common misconception: ${heat.top_misconception}` : concept.summary || undefined}
      className={`flex min-h-[262px] flex-col rounded-[12px] border px-[19px] pb-6 pt-5 ${s.tile}`}
    >
      <p className={`font-code text-[11px] ${labelTone}`}>{`// CONCEPT ${pad2(no)}`}</p>
      <p className="mt-5 text-[20px] font-medium lowercase">{concept.name}</p>
      <p className={`mt-auto font-code text-[42px] font-medium leading-none ${pct === null ? "text-line" : s.text}`}>
        {pct === null ? "—" : `${pct}%`}
      </p>
      <div className="mt-[22px] h-[5px] overflow-hidden rounded-[2px] bg-line">
        <div className={`h-full rounded-[2px] transition-all duration-700 ${s.bar}`} style={{ width: `${pct ?? 0}%` }} />
      </div>
      <p className={`mt-5 font-code text-[11px] ${s.text} ${b === "open" ? "animate-pulse" : ""}`}>
        {s.label}
        {heat && heat.n_active > 0 && b !== "open" && <span className="text-muted"> · {heat.n_active}</span>}
      </p>
    </li>
  );
}

function RoomInsight({ weakest, anyAnswers }: { weakest?: HeatmapConcept; anyAnswers: boolean }) {
  const top = weakest?.misconceptions[0];
  const [title, body, accent] = weakest
    ? [
        "↳ A useful place to pause",
        top
          ? `${top.count} answer${top.count === 1 ? "" : "s"} on ‘${weakest.name}’ share one mix-up: “${top.text}”`
          : `‘${weakest.name}’ is at ${Math.round(weakest.avg_mastery * 100)}% across ${weakest.n_active} students.`,
        "bg-coral",
      ]
    : anyAnswers
      ? ["↳ The room is keeping up", "No concept has dropped below 45% mastery.", "bg-teal"]
      : ["↳ Waiting for the first answers", "Insights appear once students start answering.", "bg-line"];
  return (
    <section className={`${CARD} relative min-h-[104px] overflow-hidden px-[23px] py-5`}>
      <span aria-hidden className={`absolute left-0 top-4 h-[70px] w-[3px] rounded-[1px] ${accent}`} />
      <p className="text-[19px] font-medium">{title}</p>
      <p className="mt-[15px] text-[15px] text-muted">{body}</p>
    </section>
  );
}

const MISC_TONE = [
  { text: "text-coral", bar: "bg-coral" },
  { text: "text-amber", bar: "bg-amber" },
  { text: "text-periwinkle", bar: "bg-periwinkle" },
];

function Misconceptions({ heat, conceptName }: { heat?: HeatmapConcept; conceptName?: string }) {
  const items = heat?.misconceptions ?? [];
  const base = heat?.n_struggling ?? 0;
  return (
    <section className={`${CARD} flex min-h-[306px] flex-col p-[23px]`}>
      <p className="font-code text-[12px] text-muted">{"// WHAT’S GETTING TANGLED"}</p>
      {items.length === 0 ? (
        <p className="mt-8 text-[14px] text-muted">
          No shared misconceptions yet. They show up here when several students trip on the same idea.
        </p>
      ) : (
        <ul className="mt-[31px] space-y-[27px]">
          {items.map((m, i) => {
            const share = base ? Math.round((m.count / base) * 100) : 0;
            return (
              <li key={m.text}>
                <div className="flex justify-between gap-6">
                  <p className="text-[14px]">“{m.text}”</p>
                  <p className={`mr-[25px] font-code text-[14px] ${MISC_TONE[i].text}`}>{share}%</p>
                </div>
                <div className="mt-3 h-1 overflow-hidden rounded-[2px] bg-line">
                  <div className={`h-full rounded-[2px] ${MISC_TONE[i].bar}`} style={{ width: `${share}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {base > 0 && (
        <p className="mt-auto pt-6 font-code text-[11px] text-muted">
          Share of {base} response{base === 1 ? "" : "s"} needing support{conceptName ? ` · ${conceptName.toLowerCase()}` : ""}
        </p>
      )}
    </section>
  );
}

function RoomPulse({ answers, modes, heatAt }: { answers: number; modes: ModeCounts; heatAt: number | null }) {
  const ago = heatAt === null ? null : Math.round((Date.now() - heatAt) / 1000);
  return (
    <section className={`${CARD} flex min-h-[306px] flex-col p-[23px]`}>
      <p className="font-code text-[12px] text-muted">{"// ROOM PULSE"}</p>
      <p className="mt-[25px] font-code text-[40px] font-medium leading-none">{answers}</p>
      <p className="mt-[18px] text-[14px] text-muted">answers this concept</p>
      <span aria-hidden className="mt-5 h-px bg-line" />
      <div className="mt-6 grid grid-cols-2">
        <div>
          <p className="font-code text-[25px] leading-none text-periwinkle">{modes.support}</p>
          <p className="mt-[14px] font-code text-[11px] text-muted">support mode</p>
        </div>
        <div>
          <p className="font-code text-[25px] leading-none text-teal">{modes.challenge}</p>
          <p className="mt-[14px] font-code text-[11px] text-muted">challenge mode</p>
        </div>
      </div>
      <p className="mt-auto pt-6 font-code text-[11px] text-muted">
        {ago === null ? "Waiting for data" : ago < 5 ? "Updated just now" : `Updated ${ago}s ago`}
      </p>
    </section>
  );
}

function TeachingCue({
  alert,
  concept,
  heat,
  no,
  onResolve,
}: {
  alert?: Escalation;
  concept?: Concept;
  heat?: HeatmapConcept;
  no: number;
  onResolve: (id: string, action: "approve" | "edit" | "dismiss", edit?: string) => Promise<void>;
}) {
  const suggestion = String(alert?.payload.recap_suggestion ?? "");
  const [text, setText] = useState(suggestion);
  const [busy, setBusy] = useState(false);
  useEffect(() => setText(suggestion), [alert?.id, suggestion]);

  if (!concept) {
    return (
      <section className={`${CARD} flex flex-col p-[23px] xl:min-h-[606px]`}>
        <Pill tone="muted" className="self-start xl:min-w-[180px]">
          ↳ TEACHING CUE
        </Pill>
        <p className="mt-[27px] text-[29px] font-medium">
          Nothing to rewind.
          <br />
          Keep going.
        </p>
        <p className="mt-5 text-[15px] text-muted">
          When a concept drops below 45% mastery with enough of the room answering, a suggested recap appears here
          for you to approve, edit or dismiss.
        </p>
      </section>
    );
  }

  const open = alert?.status === "open";
  const sent = alert?.status === "approved";
  const top = heat?.misconceptions[0];
  async function act(action: "approve" | "edit" | "dismiss") {
    if (!alert) return;
    setBusy(true);
    await onResolve(alert.id, action, action === "edit" ? text.trim() : undefined);
    setBusy(false);
  }

  return (
    <section data-testid="teaching-cue" className={`${CARD} flex flex-col p-[23px] xl:min-h-[606px]`}>
      <Pill tone={sent ? "teal" : "coral"} className="self-start xl:min-w-[180px]">
        {sent ? "✓ RECAP SENT" : "↳ TEACHING CUE"}
      </Pill>
      <p className="mt-[27px] text-[29px] font-medium">
        A small rewind.
        <br />
        A stronger next step.
      </p>
      <p className="mt-[25px] font-code text-[11px] uppercase text-coral">{`// CONCEPT ${pad2(no)} · ${concept.name}`}</p>

      {alert ? (
        <p className="mt-6 text-[17px]">{String(alert.payload.summary ?? "")}</p>
      ) : (
        <>
          <p className="mt-6 text-[19px] font-medium">{top ? "The room’s main mix-up" : "Revisit the core idea"}</p>
          {top && <p className="mt-[18px] text-[17px]">“{top.text}”</p>}
        </>
      )}

      {open && (
        <>
          <label htmlFor="recap" className="mt-6 font-code text-[10px] text-muted">
            SUGGESTED RECAP · EDIT BEFORE SENDING
          </label>
          <textarea
            id="recap"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={2000}
            rows={6}
            className="mt-[10px] w-full resize-none rounded-[8px] border border-line bg-ink p-4 text-[14px] text-fg outline-none focus:border-teal"
          />
          <p className="mt-3 text-[13px] text-muted">
            Goes to every student’s screen. You see patterns, never who answered what.
          </p>
        </>
      )}
      {sent && (
        <div className="mt-6 rounded-[8px] bg-ink p-4 text-[14px]">
          <p className="font-code text-[10px] text-teal">{`// sent to ${alert?.payload.sent_to ?? 0} students`}</p>
          <p className="mt-2">{String(alert?.payload.sent_text ?? "")}</p>
        </div>
      )}
      {!alert && concept.summary && (
        <div className="mt-6 rounded-[8px] bg-ink p-4 font-code text-[12px] text-teal">
          <p className="text-muted">{"// what the lecture said"}</p>
          <p className="mt-1">{concept.summary}</p>
        </div>
      )}
      {!alert && <p className="mt-[23px] animate-pulse text-[15px] text-muted">Drafting a suggested recap…</p>}
      {alert?.status === "dismissed" && <p className="mt-6 text-[15px] text-muted">You dismissed this alert.</p>}

      {open && (
        <div className="mt-auto flex gap-3 pt-6">
          <button
            onClick={() => act(text.trim() === suggestion.trim() ? "approve" : "edit")}
            disabled={busy || !text.trim()}
            className="h-[50px] flex-1 whitespace-pre rounded-[8px] bg-teal font-code text-[13px] font-bold text-ink transition hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Sending…" : "Send a quick check  →"}
          </button>
          <button
            onClick={() => act("dismiss")}
            disabled={busy}
            className="h-[50px] rounded-[8px] border border-line px-5 font-code text-[12px] text-muted transition hover:bg-raised disabled:opacity-50"
          >
            Dismiss
          </button>
        </div>
      )}
    </section>
  );
}

function Footer({
  status,
  ended,
  replay,
  speed,
  onSpeed,
  onStart,
  onPause,
  error,
  live,
  slides,
  onSlides,
  opsHref,
  toReview,
}: {
  live: ReturnType<typeof useLiveTranscript>;
  slides: string | null;
  onSlides: (f: File) => void;
  opsHref: string;
  toReview: number;
  status: ConnStatus;
  ended: boolean;
  replay: ReplayStatus | null;
  speed: number;
  onSpeed: (v: number) => void;
  onStart: () => void;
  onPause: () => void;
  error: string | null;
}) {
  const health =
    status === "connected"
      ? "● Session healthy"
      : status === "error"
        ? "● Session not found"
        : status === "closed"
          ? "● Disconnected"
          : "● Reconnecting…";
  const started = (replay?.position_s ?? 0) > 0;
  const finished = !!replay && replay.duration_s > 0 && replay.position_s >= replay.duration_s;
  const progress = replay && replay.duration_s ? Math.min(1, replay.position_s / replay.duration_s) : 0;
  const fileRef = useRef<HTMLInputElement>(null);
  const usedLive = live.live || live.elapsed > 0;
  return (
    <footer className="flex flex-wrap items-center gap-x-6 gap-y-4 px-5 pb-8 pt-[34px] font-code text-[11px] text-muted sm:px-10">
      <p data-testid="conn-status" className="whitespace-pre">
        <span className={status === "connected" ? "" : "text-amber"}>{health}</span>
        {"   /   "}
        {live.live ? "microphone live" : replay?.running ? "replaying lecture" : "transcript feed"}
      </p>

      <div className="flex flex-wrap items-center gap-3" aria-label="Demo replay">
        {replay?.running ? (
          <button onClick={onPause} className="rounded-[8px] border border-line px-3 py-[7px] text-fg hover:bg-panel">
            ❚❚ Pause replay
          </button>
        ) : (
          <button
            onClick={onStart}
            disabled={finished || ended || usedLive}
            className="rounded-[8px] border border-teal px-3 py-[7px] text-teal hover:bg-panel disabled:border-line disabled:text-muted"
          >
            {ended ? "Lecture ended" : finished ? "Replay finished" : started ? "▶ Resume replay" : "▶ Start demo replay"}
          </button>
        )}
        <div className="flex overflow-hidden rounded-[8px] border border-line" role="group" aria-label="Replay speed">
          {SPEEDS.map((v) => (
            <button
              key={v}
              onClick={() => onSpeed(v)}
              aria-pressed={speed === v}
              className={`px-[10px] py-[7px] ${speed === v ? "bg-panel text-teal" : "hover:text-fg"}`}
            >
              {v}x
            </button>
          ))}
        </div>
        <div className="h-1 w-16 overflow-hidden rounded-[2px] bg-line">
          <div className="h-full bg-teal transition-all duration-300" style={{ width: `${progress * 100}%` }} />
        </div>
        <span>
          {mmss(replay?.position_s ?? 0)} / {mmss(replay?.duration_s ?? 0)}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-3" aria-label="Live mode and slides">
        {live.supported && (
          <button
            onClick={live.live ? live.stop : live.start}
            disabled={ended || started}
            title={started ? "This session is using the demo replay" : "Transcribe your microphone (Chrome or Edge)"}
            className={`rounded-[8px] border px-3 py-[7px] hover:bg-panel disabled:opacity-40 ${
              live.live ? "border-coral text-coral" : "border-line text-fg"
            }`}
          >
            {live.live ? "■ Stop microphone" : "● Go live"}
          </button>
        )}
        {live.live && live.lastText && <span className="max-w-[220px] truncate">“{live.lastText}”</span>}
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onSlides(f);
            e.target.value = "";
          }}
        />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={ended}
          className="rounded-[8px] border border-line px-3 py-[7px] hover:bg-panel hover:text-fg disabled:opacity-40"
        >
          {slides ? `✓ ${slides}` : "⬆ Slides"}
        </button>
        {error && <span className="text-coral">{error}</span>}
      </div>

      <Link href={opsHref} className="ml-auto whitespace-pre hover:text-fg" data-testid="ops-link">
        {"Ops & review"}
        {toReview > 0 && <span className="ml-2 rounded-full bg-coral px-[7px] py-[2px] font-bold text-ink">{toReview}</span>}
        {"  →"}
      </Link>

      <button
        onClick={() => document.documentElement.requestFullscreen?.().catch(() => {})}
        className="whitespace-pre hover:text-fg"
      >
        {"Projector view  ↗"}
      </button>
    </footer>
  );
}
