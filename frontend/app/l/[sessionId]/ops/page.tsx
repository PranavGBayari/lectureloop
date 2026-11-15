"use client";
// Observability and the TA review queue. Everything on the left is an aggregate over this
// session's event log (guardrail checks, latency, tokens, grader confidence, red-team pass rate).
// The queue on the right is the human-in-the-loop side: low-confidence grades a TA can override,
// students at an impasse, and blocked injection attempts. Answers are shown without names.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Pill, type PillTone } from "@/components/companion/Pill";
import { InlineCode } from "@/components/InlineCode";
import { api, ApiError, type Escalation, type OpsResponse, type SessionPublic, type Verdict } from "@/lib/api";

const CARD = "rounded-[12px] border border-line bg-panel p-[23px]";
const LABEL = "font-code text-[12px] text-muted";
const POLL_MS = 3000;

const GUARDRAIL_LABEL: Record<string, string> = {
  input_validation: "Input validation",
  injection: "Injection filter",
  schema_validation: "Schema validation",
  grounding: "Grounding check",
  answer_leak: "Answer-leak check",
};
const TYPE: Record<string, { label: string; tone: PillTone }> = {
  low_confidence_grade: { label: "◐ LOW-CONFIDENCE GRADE", tone: "amber" },
  impasse: { label: "↺ IMPASSE", tone: "periwinkle" },
  injection: { label: "⛨ INJECTION BLOCKED", tone: "coral" },
  weak_concept: { label: "↓ WEAK CONCEPT", tone: "coral" },
};

export default function OpsPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<SessionPublic | null>(null);
  const [ops, setOps] = useState<OpsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setOps(await api.ops(sessionId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [sessionId]);

  useEffect(() => {
    api.getSession(sessionId).then(setSession, () => setSession(null));
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [sessionId, load]);

  async function resolve(e: Escalation, action: "approve" | "dismiss" | "override", verdict?: Verdict) {
    setBusy(e.id);
    try {
      await api.resolveEscalation(e.id, action, { verdict });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const queue = ops?.escalations.filter((e) => e.type !== "weak_concept") ?? [];
  const open = queue.filter((e) => e.status === "open");
  const done = queue.filter((e) => e.status !== "open");

  return (
    <div className="mx-auto flex min-h-dvh max-w-[1440px] flex-col px-5 pb-10 sm:px-10">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-line py-6">
        <div>
          <p className="flex items-baseline gap-[22px]">
            <span className="font-code text-[17px] font-medium">
              <span className="text-teal">↻</span> LectureLoop
            </span>
            <span className="font-code text-[11px] text-muted">/ OPS</span>
          </p>
          <h1 className="mt-5 text-[32px] font-medium">Guardrails and review queue</h1>
          <p className="mt-[10px] font-code text-[13px] text-muted">{session?.title ?? "…"}</p>
        </div>
        <Link href={`/l/${sessionId}`} className="font-code text-[12px] text-muted hover:text-fg">
          ← Back to the dashboard
        </Link>
      </header>

      {error && (
        <p role="alert" className="mt-6 font-code text-[12px] text-coral">
          {error}
        </p>
      )}

      {!ops ? (
        <div className="mt-8 grid gap-6 xl:grid-cols-[924fr_400fr]">
          <div className="h-96 animate-pulse rounded-[12px] bg-panel" />
          <div className="h-96 animate-pulse rounded-[12px] bg-panel" />
        </div>
      ) : (
        <main className="mt-8 grid gap-6 xl:grid-cols-[860fr_464fr] xl:items-start">
          <div className="flex min-w-0 flex-col gap-6">
            <section className="grid grid-cols-2 gap-4 md:grid-cols-4" aria-label="Totals">
              <Stat label="answers graded" value={ops.n_answers} />
              <Stat label="injections blocked" value={ops.n_blocked} tone={ops.n_blocked ? "text-coral" : undefined} />
              <Stat label="model calls" value={ops.llm_calls} />
              <Stat
                label="model tokens per answer"
                value={Math.round(ops.tokens_per_turn)}
                hint="All input and output tokens across every agent (segmenting, question writing, grading, tutoring, classifiers), divided by the number of answers submitted."
              />
            </section>

            <div className="grid gap-6 md:grid-cols-[1fr_300px]">
              <Guardrails ops={ops} />
              <Redteam ops={ops} />
            </div>

            <div className="grid gap-6 md:grid-cols-2">
              <Latency ops={ops} />
              <Confidence ops={ops} />
            </div>
          </div>

          <section aria-label="Review queue" className="flex flex-col gap-4">
            <div className="flex items-baseline justify-between">
              <h2 className="text-[19px] font-medium">Review queue</h2>
              <p className="font-code text-[11px] text-muted" data-testid="queue-open">
                {open.length} open
              </p>
            </div>
            {open.length === 0 && (
              <p className="rounded-[12px] border border-dashed border-line p-6 text-[14px] text-muted">
                Nothing waiting. Grades the grader is unsure of, students stuck on one concept and blocked injection
                attempts land here.
              </p>
            )}
            {open.map((e) => (
              <QueueCard key={e.id} e={e} busy={busy === e.id} onResolve={resolve} />
            ))}
            {done.length > 0 && (
              <div className="mt-2">
                <p className="font-code text-[11px] text-muted">{"// RESOLVED"}</p>
                <ul className="mt-3 space-y-2">
                  {done.map((e) => (
                    <li key={e.id} className="flex items-center justify-between gap-3 rounded-[8px] border border-line px-4 py-3">
                      <span className="truncate text-[13px] text-muted">
                        {String(e.payload.concept_name ?? e.payload.text ?? TYPE[e.type]?.label)}
                      </span>
                      <span className="shrink-0 font-code text-[10px] text-teal">
                        {e.status}
                        {e.payload.ta_verdict ? ` → ${e.payload.ta_verdict}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </main>
      )}
    </div>
  );
}

function Stat({ label, value, tone, hint }: { label: string; value: number; tone?: string; hint?: string }) {
  return (
    <div title={hint} className="rounded-[12px] border border-line bg-panel px-[19px] py-5">
      <p className={`font-code text-[32px] font-medium leading-none ${tone ?? "text-fg"}`}>{value.toLocaleString()}</p>
      <p className="mt-3 font-code text-[11px] text-muted">{label}</p>
    </div>
  );
}

function Guardrails({ ops }: { ops: OpsResponse }) {
  return (
    <section className={CARD}>
      <p className={LABEL}>{"// GUARDRAIL PIPELINE"}</p>
      <table className="mt-5 w-full text-left">
        <thead>
          <tr className="font-code text-[10px] text-muted">
            <th className="pb-3 font-normal">check</th>
            <th className="pb-3 text-right font-normal">ran</th>
            <th className="pb-3 text-right font-normal">triggered</th>
          </tr>
        </thead>
        <tbody>
          {ops.guardrails.map((g) => (
            <tr key={g.name} className="border-t border-line" data-guardrail={g.name}>
              <td className="py-3 text-[14px]">{GUARDRAIL_LABEL[g.name] ?? g.name}</td>
              <td className="py-3 text-right font-code text-[13px] text-muted">{g.checks}</td>
              <td className={`py-3 text-right font-code text-[13px] ${g.triggered ? "text-coral" : "text-teal"}`}>
                {g.triggered}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-4 text-[12px] text-muted">
        “Triggered” means the check stopped or changed something: a blocked answer, a rejected question, a retried
        model output.
      </p>
    </section>
  );
}

function Redteam({ ops }: { ops: OpsResponse }) {
  const r = ops.redteam;
  const failed = r.cases.filter((c) => !c.passed);
  const ok = r.rate >= 0.9;
  return (
    <section className={CARD}>
      <p className={LABEL}>{"// RED-TEAM SUITE"}</p>
      <p data-testid="redteam-rate" className={`mt-6 font-code text-[42px] font-medium leading-none ${ok ? "text-teal" : "text-coral"}`}>
        {Math.round(r.rate * 100)}%
      </p>
      <p className="mt-4 text-[14px] text-muted">
        {r.passed} of {r.total} injection and leak attempts handled correctly.
      </p>
      <div className="mt-5 h-[5px] overflow-hidden rounded-[2px] bg-line">
        <div className={`h-full ${ok ? "bg-teal" : "bg-coral"}`} style={{ width: `${r.rate * 100}%` }} />
      </div>
      {failed.length > 0 && (
        <p className="mt-4 font-code text-[11px] text-coral">failed: {failed.map((c) => c.id).join(", ")}</p>
      )}
      <p className="mt-4 font-code text-[10px] text-muted">gate: 90%</p>
    </section>
  );
}

function Latency({ ops }: { ops: OpsResponse }) {
  const max = Math.max(1, ...ops.latency.map((l) => l.p95_ms));
  return (
    <section className={CARD}>
      <p className={LABEL}>{"// LATENCY BY AGENT"}</p>
      {ops.latency.length === 0 ? (
        <p className="mt-6 text-[14px] text-muted">No model calls yet.</p>
      ) : (
        <ul className="mt-5 space-y-4">
          {ops.latency.map((l) => (
            <li key={l.agent}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[14px]">{l.agent.replaceAll("_", " ")}</span>
                <span className="font-code text-[11px] text-muted">
                  p50 <span className="text-fg">{l.p50_ms} ms</span> · p95 <span className="text-fg">{l.p95_ms} ms</span>
                </span>
              </div>
              {/* One bar: solid to p50, faint to p95. */}
              <div className="relative mt-2 h-1 rounded-[2px] bg-line">
                <div className="absolute inset-y-0 left-0 rounded-[2px] bg-teal/35" style={{ width: `${(l.p95_ms / max) * 100}%` }} />
                <div className="absolute inset-y-0 left-0 rounded-[2px] bg-teal" style={{ width: `${(l.p50_ms / max) * 100}%` }} />
              </div>
              <p className="mt-[6px] font-code text-[10px] text-muted">
                {l.calls} calls · {(l.tokens_in + l.tokens_out).toLocaleString()} tokens
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Confidence({ ops }: { ops: OpsResponse }) {
  const h = ops.confidence_histogram;
  const max = Math.max(1, ...h);
  const low = h.slice(0, 6).reduce((a, b) => a + b, 0);
  return (
    <section className={CARD}>
      <p className={LABEL}>{"// GRADER CONFIDENCE"}</p>
      <div className="mt-6 flex h-[120px] items-end gap-[6px]" role="img" aria-label={`Grader confidence histogram, ${low} grades below 0.6`}>
        {h.map((n, i) => (
          <div key={i} className="flex h-full flex-1 flex-col justify-end" title={`${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}: ${n}`}>
            {n > 0 && <span className="mb-1 text-center font-code text-[10px] text-muted">{n}</span>}
            <div
              className={`rounded-t-[2px] ${i < 6 ? "bg-amber" : "bg-teal"}`}
              style={{ height: `${Math.max(n ? 4 : 1, (n / max) * 92)}%`, opacity: n ? 1 : 0.25 }}
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between font-code text-[9px] text-muted">
        <span>0.0</span>
        <span>0.6</span>
        <span>1.0</span>
      </div>
      <p className="mt-4 text-[12px] text-muted">
        <span className="text-amber">●</span> below 0.6 goes to a TA ({low} so far) · <span className="text-teal">●</span>{" "}
        graded automatically
      </p>
    </section>
  );
}

function QueueCard({
  e,
  busy,
  onResolve,
}: {
  e: Escalation;
  busy: boolean;
  onResolve: (e: Escalation, action: "approve" | "dismiss" | "override", verdict?: Verdict) => void;
}) {
  const t = TYPE[e.type] ?? { label: e.type, tone: "muted" as PillTone };
  const p = e.payload as Record<string, string | number | null | undefined>;
  const btn = "rounded-[8px] border border-line px-3 py-2 font-code text-[11px] transition hover:bg-raised disabled:opacity-50";
  return (
    <article className={CARD} data-escalation={e.type}>
      <Pill tone={t.tone}>{t.label}</Pill>
      {p.concept_name && <p className="mt-4 font-code text-[10px] lowercase text-muted">{`// ${p.concept_name}`}</p>}

      {e.type === "injection" ? (
        <>
          <p className="mt-3 rounded-[6px] bg-ink p-3 font-code text-[12px] text-coral">{String(p.text)}</p>
          <p className="mt-3 text-[13px] text-muted">
            Caught by the {String(p.stage)} stage{p.matched ? ` (“${p.matched}”)` : ""}. Not graded; the student was asked
            to answer in their own words.
          </p>
        </>
      ) : (
        <>
          <p className="mt-3 text-[14px] font-medium">
            <InlineCode text={String(p.question)} />
          </p>
          <p className="mt-3 rounded-[6px] bg-ink p-3 text-[14px]">“{String(p.answer)}”</p>
          <p className="mt-3 font-code text-[11px] text-muted">
            graded <span className="text-fg">{String(p.verdict)}</span> · confidence{" "}
            <span className={Number(p.confidence) < 0.6 ? "text-amber" : "text-fg"}>{Number(p.confidence).toFixed(2)}</span>
            {e.type === "impasse" && <> · {String(p.wrong_count)} wrong on this concept</>}
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer font-code text-[10px] text-muted hover:text-fg">RUBRIC</summary>
            <p className="mt-2 text-[13px] text-muted">{String(p.rubric)}</p>
          </details>
        </>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        {e.type === "low_confidence_grade" && (
          <>
            {(["correct", "partial", "wrong"] as Verdict[]).map((v) => (
              <button key={v} disabled={busy} onClick={() => onResolve(e, "override", v)} className={`${btn} text-fg`}>
                Mark {v}
              </button>
            ))}
            <button disabled={busy} onClick={() => onResolve(e, "approve")} className={`${btn} text-muted`}>
              Keep grade
            </button>
          </>
        )}
        {e.type === "impasse" && (
          <>
            <button disabled={busy} onClick={() => onResolve(e, "approve")} className={`${btn} text-fg`}>
              Give the idea and move them on
            </button>
            <button disabled={busy} onClick={() => onResolve(e, "dismiss")} className={`${btn} text-muted`}>
              Leave it
            </button>
          </>
        )}
        {e.type === "injection" && (
          <button disabled={busy} onClick={() => onResolve(e, "approve")} className={`${btn} text-muted`}>
            Acknowledge
          </button>
        )}
      </div>
    </article>
  );
}
