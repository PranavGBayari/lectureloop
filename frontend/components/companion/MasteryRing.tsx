// Overall-mastery ring and score trend. Both are data-driven, so they're drawn here rather than
// using the static Figma exports; strokes and colours match the design (track #23374f, 8px, teal).
export function MasteryRing({ value, size }: { value: number | null; size: number }) {
  const r = 69;
  const c = 2 * Math.PI * r;
  const pct = value ?? 0;
  return (
    <svg width={size} height={size} viewBox="0 0 154 154" aria-hidden className="block -rotate-90">
      <circle cx="77" cy="77" r={r} fill="none" strokeWidth="8" className="stroke-line" />
      {value !== null && (
        <circle
          cx="77"
          cy="77"
          r={r}
          fill="none"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          className="stroke-teal transition-[stroke-dashoffset] duration-700"
        />
      )}
    </svg>
  );
}

export function ScoreTrend({ values, className = "" }: { values: number[]; className?: string }) {
  const w = 220,
    h = 44;
  if (values.length === 0) {
    return <div className={`flex items-end text-[10px] text-muted ${className}`}>Answer a question to start your trend.</div>;
  }
  if (values.length === 1) {
    // One answer so far: a single point at "now", at its height.
    return (
      <svg viewBox={`0 0 ${w} ${h}`} className={className} aria-label="Mastery after your first answer">
        <circle cx={w - 4} cy={1 + (1 - values[0]) * (h - 2)} r="3" className="fill-teal" />
      </svg>
    );
  }
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${1 + (1 - v) * (h - 2)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={className} aria-label="Mastery after each answer">
      <polyline points={pts} fill="none" strokeWidth="2" vectorEffect="non-scaling-stroke" className="stroke-teal" />
    </svg>
  );
}
