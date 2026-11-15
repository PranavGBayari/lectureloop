"use client";
// Runs the ElevenLabs Scribe hook and reports its state upward. It exists so the SDK (~170 kB, and
// not tree-shakeable) is code-split: useLiveEngine loads this with next/dynamic only when the server
// says ElevenLabs is configured, so a lecturer on the browser engine never downloads it.
import { useEffect } from "react";
import { type ScribeState, useScribeTranscript } from "@/lib/useScribeTranscript";

export default function ScribeHost({ sessionId, onState }: { sessionId: string; onState: (s: ScribeState) => void }) {
  const scribe = useScribeTranscript(sessionId);
  // Depend on the fields, not the object: the hook returns a fresh object on every render.
  useEffect(() => {
    onState(scribe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scribe.live, scribe.error, scribe.lastText, scribe.elapsed, scribe.partial, scribe.reconnecting, scribe.start, scribe.stop]);
  return null;
}
