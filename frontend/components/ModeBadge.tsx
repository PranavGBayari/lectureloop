import { Pill, type PillTone } from "@/components/companion/Pill";
import type { Mode } from "@/lib/api";

const STYLE: Record<Mode, { label: string; tone: PillTone; blurb: [string, string] }> = {
  normal: { label: "→ steady pace", tone: "teal", blurb: ["Questions at your level.", "Your pace, not a race."] },
  support: { label: "↗ support mode", tone: "periwinkle", blurb: ["More scaffolding.", "Same destination."] },
  challenge: { label: "↑ challenge mode", tone: "amber", blurb: ["Fewer hints, harder questions.", "You've earned it."] },
};

export function ModeBadge({ mode }: { mode: Mode }) {
  const s = STYLE[mode];
  return (
    <Pill data-testid="mode-badge" tone={s.tone} className="xl:w-[185px]">
      {s.label}
    </Pill>
  );
}

export function modeBlurb(mode: Mode) {
  return STYLE[mode].blurb;
}
