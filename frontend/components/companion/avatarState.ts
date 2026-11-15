// Prof. Cube's mood, driven by tutor events on the student WebSocket.
//   question -> idle, answer sent -> thinking, correct -> happy, partial/wrong -> nudge, hint -> explaining
// Reactions (happy, nudge, explaining) are held for a moment so they are actually seen: the server
// often sends feedback, a hint and the next question within a few hundred milliseconds. While a
// reaction is held, later requests queue (latest wins) and play when it ends; "thinking" is the
// student's own action, so it always applies at once.
import { useCallback, useEffect, useRef, useState } from "react";

export type AvatarState = "idle" | "thinking" | "happy" | "nudge" | "explaining";

const HOLD_MS: Partial<Record<AvatarState, number>> = { happy: 2600, nudge: 2600, explaining: 3200 };

export function useAvatarState() {
  const [state, setState] = useState<AvatarState>("idle");
  const holdUntil = useRef(0);
  const queued = useRef<AvatarState | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const apply = useCallback((next: AvatarState) => {
    clearTimeout(timer.current);
    setState(next);
    const hold = HOLD_MS[next];
    if (!hold) {
      holdUntil.current = 0;
      return;
    }
    holdUntil.current = Date.now() + hold;
    timer.current = setTimeout(() => {
      holdUntil.current = 0;
      const q = queued.current;
      queued.current = null;
      apply(q ?? "idle");
    }, hold);
  }, []);

  const request = useCallback(
    (next: AvatarState) => {
      if (next !== "thinking" && Date.now() < holdUntil.current) {
        queued.current = next;
        return;
      }
      queued.current = null;
      apply(next);
    },
    [apply],
  );

  useEffect(() => () => clearTimeout(timer.current), []);
  return [state, request] as const;
}
