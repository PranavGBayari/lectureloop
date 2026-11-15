import type { Concept } from "@/lib/api";

/** The student's learning thread: one numbered chip per concept, in lecture order.
 *  Teal = taught, amber on deep teal = the concept you're answering on, grey = still being taught. */
export function ConceptTimeline({
  concepts,
  currentId,
}: {
  concepts: Concept[];
  currentId?: string | null;
}) {
  if (concepts.length === 0) {
    return <p className="font-code text-[11px] text-muted">Concepts appear here as the lecture goes.</p>;
  }
  return (
    <ol className="flex flex-wrap items-center gap-x-[10px] gap-y-[6px] xl:gap-x-[9px]" aria-label="Concepts so far">
      {concepts.map((c, i) => {
        const open = c.t_end == null;
        const current = c.id === currentId;
        const tone = current
          ? "border-teal bg-teal-deep text-amber"
          : open
            ? "border-muted bg-panel text-muted"
            : "border-teal bg-panel text-teal";
        return (
          <li key={c.id} className="flex items-center gap-[9px]">
            {i > 0 && (
              <span aria-hidden className="hidden font-code text-[12px] text-line xl:inline">
                —
              </span>
            )}
            <span
              title={c.summary || undefined}
              aria-current={current ? "step" : undefined}
              className={`inline-flex h-[30px] items-center whitespace-pre rounded-full border px-[11px] font-code text-[10px] lowercase xl:h-[34px] xl:px-3 xl:text-[11px] ${tone} ${open && !current ? "animate-pulse" : ""}`}
            >
              {String(i + 1).padStart(2, "0")}
              {"  "}
              {c.name}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function upsertConcept(list: Concept[], c: Concept): Concept[] {
  const i = list.findIndex((x) => x.id === c.id);
  const next = i === -1 ? [...list, c] : list.map((x) => (x.id === c.id ? c : x));
  return next.sort((a, b) => a.t_start - b.t_start);
}
