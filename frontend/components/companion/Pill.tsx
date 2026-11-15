// Rounded mono "chip" used across the companion (● live, ✓ correct, ↗ support mode, …).
const TONE = {
  teal: "border-teal text-teal",
  muted: "border-muted text-muted",
  amber: "border-amber text-amber",
  periwinkle: "border-periwinkle text-periwinkle",
  coral: "border-coral text-coral",
} as const;

export type PillTone = keyof typeof TONE;

export function Pill({
  tone = "teal",
  className = "",
  children,
  ...rest
}: { tone?: PillTone; className?: string; children: React.ReactNode } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      {...rest}
      className={`inline-flex h-[30px] items-center whitespace-pre rounded-full border bg-panel px-[11px] font-code text-[10px] xl:h-[34px] xl:px-3 xl:text-[11px] ${TONE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
