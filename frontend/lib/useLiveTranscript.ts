"use client";
// Live mode: the browser's Web Speech API transcribes the lecturer's microphone and each finished
// phrase is posted to POST /sessions/{id}/transcript, the same endpoint the demo replay feeds.
// Chrome and Edge only (webkitSpeechRecognition); elsewhere `supported` is false.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";

type Result = { isFinal: boolean; 0: { transcript: string } };
type Recognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<Result> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
};

function recognitionCtor(): (new () => Recognition) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return ((w.SpeechRecognition ?? w.webkitSpeechRecognition) as new () => Recognition) ?? null;
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone permission was denied.",
  "service-not-allowed": "Speech recognition is blocked in this browser.",
  "audio-capture": "No microphone found.",
  network: "Speech recognition needs a network connection.",
};

export function useLiveTranscript(sessionId: string) {
  const [supported, setSupported] = useState(false);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastText, setLastText] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const rec = useRef<Recognition | null>(null);
  const wanted = useRef(false);
  const clock = useRef({ t0: 0, lastEnd: 0 });

  useEffect(() => setSupported(recognitionCtor() !== null), []);

  const stop = useCallback(() => {
    wanted.current = false;
    rec.current?.stop();
    setLive(false);
  }, []);

  const start = useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor) return;
    setError(null);
    const r = new Ctor();
    r.continuous = true;
    r.interimResults = false;
    r.lang = "en-GB";
    if (clock.current.t0 === 0) clock.current.t0 = performance.now();
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res[0].transcript.trim();
        if (!res.isFinal || !text) continue;
        const tEnd = (performance.now() - clock.current.t0) / 1000;
        const chunk = { t_start: clock.current.lastEnd, t_end: tEnd, text };
        clock.current.lastEnd = tEnd;
        setLastText(text);
        setElapsed(tEnd);
        api.addTranscript(sessionId, [chunk]).catch((err) => {
          setError(err instanceof ApiError ? err.message : String(err));
          stop();
        });
      }
    };
    r.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return; // silence is not an error
      setError(ERRORS[e.error] ?? `Speech recognition error: ${e.error}`);
      stop();
    };
    // Chrome ends recognition after a pause; keep listening until the lecturer stops it.
    r.onend = () => {
      if (wanted.current) {
        try {
          r.start();
        } catch {
          setLive(false);
        }
      }
    };
    rec.current = r;
    wanted.current = true;
    try {
      r.start();
      setLive(true);
    } catch {
      setError("Couldn't start the microphone.");
    }
  }, [sessionId, stop]);

  useEffect(() => stop, [stop]);

  return { supported, live, error, lastText, elapsed, start, stop };
}
