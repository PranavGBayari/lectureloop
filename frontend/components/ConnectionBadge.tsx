import type { ConnStatus } from "@/lib/useSession";

const STYLES: Record<ConnStatus, { dot: string; label: string }> = {
  connecting: { dot: "text-amber animate-pulse", label: "Connecting to lecture…" },
  reconnecting: { dot: "text-amber animate-pulse", label: "Reconnecting to lecture…" },
  connected: { dot: "text-muted", label: "Connected to lecture" },
  closed: { dot: "text-muted", label: "Disconnected" },
  error: { dot: "text-amber", label: "Seat not found" },
};

/** Low-key connection line for the page footer. */
export function ConnectionBadge({ status }: { status: ConnStatus }) {
  const s = STYLES[status];
  return (
    <span data-testid="conn-status" className="font-code text-[10px] text-muted">
      <span className={s.dot}>●</span> {s.label}
    </span>
  );
}
