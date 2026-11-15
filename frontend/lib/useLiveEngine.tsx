"use client";
// Chooses the live transcription engine for the lecturer's "Go live" button:
//   ElevenLabs Scribe v2 Realtime when the server says it is configured (GET /health -> elevenlabs),
//   otherwise the browser's Web Speech API (Chrome and Edge).
// If ElevenLabs cannot start for a reason other than the microphone (no token, network), it falls
// back to the browser engine for that session and says so. A denied microphone is not retried with
// the other engine: both need the same permission.
//
// The ElevenLabs SDK is heavy, so its hook runs inside <ScribeHost>, which is loaded on demand and
// rendered through the `host` element this hook returns. The page must render `{live.host}`.
import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useLiveTranscript } from "@/lib/useLiveTranscript";
import type { ScribeState } from "@/lib/useScribeTranscript";

const ScribeHost = dynamic(() => import("@/components/ScribeHost"), { ssr: false });

export type Engine = "elevenlabs" | "browser";
export const ENGINE_LABEL: Record<Engine, string> = { elevenlabs: "ElevenLabs", browser: "browser" };

const IDLE = { live: false, reconnecting: false, error: null, lastText: "", elapsed: 0, partial: "" };

export function useLiveEngine(sessionId: string) {
  const web = useLiveTranscript(sessionId);
  const [elevenlabs, setElevenlabs] = useState<boolean | null>(null); // null until /health answers
  const [scribe, setScribe] = useState<ScribeState | null>(null);     // null until the SDK chunk has loaded
  const [engine, setEngine] = useState<Engine>("browser");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(
      (h) => {
        setElevenlabs(h.elevenlabs === true);
        if (h.elevenlabs === true) setEngine("elevenlabs");
      },
      () => setElevenlabs(false),
    );
  }, []);

  const start = useCallback(async () => {
    setNotice(null);
    if (elevenlabs && scribe) {
      setEngine("elevenlabs");
      const r = await scribe.start();
      if (r === "ok" || r === "mic") return;
      if (web.supported) {
        setEngine("browser");
        setNotice("ElevenLabs is unavailable, so this lecture is using the browser's speech recognition.");
        web.start();
      }
      return;
    }
    setEngine("browser");
    web.start();
  }, [elevenlabs, scribe, web]);

  const stop = useCallback(() => (engine === "elevenlabs" ? scribe?.stop() : web.stop()), [engine, scribe, web]);

  const a = engine === "elevenlabs" ? (scribe ?? IDLE) : web;
  return {
    /** Render this once in the page: it mounts the ElevenLabs engine when it is configured. */
    host: elevenlabs ? <ScribeHost sessionId={sessionId} onState={setScribe} /> : null,
    engine,
    // Go live needs at least one engine; before /health answers, assume the browser one.
    supported: elevenlabs === true || web.supported,
    loading: elevenlabs === true && scribe === null, // ElevenLabs chosen, SDK still downloading
    live: a.live,
    reconnecting: engine === "elevenlabs" && (scribe?.reconnecting ?? false),
    error: a.error ?? notice,
    lastText: a.lastText,
    partial: engine === "elevenlabs" ? (scribe?.partial ?? "") : "",
    elapsed: a.elapsed,
    start,
    stop,
  };
}
