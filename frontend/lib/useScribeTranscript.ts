"use client";
// Live lecture transcription with ElevenLabs Scribe v2 Realtime. Same interface as
// useLiveTranscript (the browser Web Speech engine), plus `partial` (words as they are spoken)
// and `reconnecting`.
//
// Flow: the backend mints a single-use token (the API key never reaches the browser), useScribe
// opens a WebSocket to ElevenLabs and streams the microphone, and each committed segment is
// POSTed to /sessions/{id}/transcript, the same endpoint the demo replay and Web Speech use.
//
// Resilience: a dropped connection is retried with a FRESH token (a token is consumed on connect,
// so an old one can never be reused) and exponential backoff. Denied microphone, an exhausted
// quota or unaccepted terms are not retried; they stop with a message.
import { CommitStrategy, useScribe } from "@elevenlabs/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";

export const SCRIBE_MODEL = "scribe_v2_realtime";
const VAD_SILENCE_S = 1.0;       // a pause this long commits the segment
const FORCE_COMMIT_AFTER_S = 20; // a lecturer who never pauses still gets a segment this often
const MAX_RETRIES = 5;

const SCRIBE_OPTIONS = {
  modelId: SCRIBE_MODEL,
  commitStrategy: CommitStrategy.VAD,
  vadSilenceThresholdSecs: VAD_SILENCE_S,
  languageCode: "en",
  microphone: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  // Optional: a regional endpoint (e.g. wss://api.eu.residency.elevenlabs.io) for data residency.
  // The default is wss://api.elevenlabs.io. The token endpoint's region must match the key.
  ...(process.env.NEXT_PUBLIC_ELEVENLABS_WS_URL ? { baseUri: process.env.NEXT_PUBLIC_ELEVENLABS_WS_URL } : {}),
};

const MIC_DENIED = "Microphone permission was denied. Allow it in the browser's address bar, then press Go live again.";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function isMicError(e: unknown): boolean {
  const name = (e as { name?: string })?.name ?? "";
  const msg = e instanceof Error ? e.message : typeof e === "string" ? e : "";
  return /NotAllowed|PermissionDenied|NotFound|NotReadable|Overconstrained/i.test(name) ||
    /permission|denied|not allowed|microphone|getUserMedia|no (audio )?device/i.test(msg);
}

type Open = "ok" | "mic" | "token" | "connect";

export function useScribeTranscript(sessionId: string) {
  const [live, setLive] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastText, setLastText] = useState("");
  const [elapsed, setElapsed] = useState(0);

  const wanted = useRef(false);        // the lecturer wants the microphone on
  const busy = useRef(false);          // a (re)connect attempt is in flight
  const retries = useRef(0);
  const clock = useRef({ t0: 0, lastEnd: 0, lastCommit: 0 });
  const handlers = useRef<Record<string, (...a: never[]) => void>>({});

  // The SDK may keep the callbacks it first saw, so they all go through this stable indirection.
  const scribe = useScribe({
    ...SCRIBE_OPTIONS,
    onCommittedTranscript: (d) => (handlers.current.commit as (d: { text: string }) => void)?.(d),
    onSessionStarted: () => handlers.current.started?.(),
    onDisconnect: () => handlers.current.disconnected?.(),
    onError: (e) => (handlers.current.error as (e: unknown) => void)?.(e),
    onAuthError: () => handlers.current.disconnected?.(), // an expired or used token: reconnect with a new one
    onQuotaExceededError: () => (handlers.current.fatal as (m: string) => void)?.("Your ElevenLabs quota is used up."),
    onUnacceptedTermsError: () =>
      (handlers.current.fatal as (m: string) => void)?.("Accept the ElevenLabs terms in your ElevenLabs account first."),
  });
  const scribeRef = useRef(scribe);
  scribeRef.current = scribe;

  const giveUp = useCallback((message: string) => {
    wanted.current = false;
    setError(message);
    setLive(false);
    setReconnecting(false);
    scribeRef.current.disconnect();
  }, []);

  /** Fetch a fresh token and connect. Never throws; says what went wrong. */
  const open = useCallback(async (): Promise<Open> => {
    let token: string;
    try {
      token = (await api.scribeToken(sessionId)).token;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not get a transcription token.");
      return "token";
    }
    try {
      await scribeRef.current.connect({ ...SCRIBE_OPTIONS, token });
      return "ok";
    } catch (e) {
      return isMicError(e) ? "mic" : "connect";
    }
  }, [sessionId]);

  const reconnect = useCallback(async () => {
    if (busy.current || !wanted.current) return;
    busy.current = true;
    try {
      while (wanted.current) {
        if (retries.current >= MAX_RETRIES) {
          giveUp("Lost the connection to ElevenLabs and could not restore it. Press Go live to try again.");
          return;
        }
        setReconnecting(true);
        await sleep(1000 * 2 ** retries.current); // 1s, 2s, 4s, 8s, 16s
        retries.current += 1;
        if (!wanted.current) return;
        const r = await open();
        if (r === "ok") return; // onSessionStarted clears the retry count
        if (r === "mic") {
          giveUp(MIC_DENIED);
          return;
        }
      }
    } finally {
      busy.current = false;
    }
  }, [open, giveUp]);

  handlers.current = {
    commit: ({ text }: { text: string }) => {
      const t = text.trim();
      if (!t) return;
      const c = clock.current;
      const now = (performance.now() - c.t0) / 1000;
      // The commit lands one pause after the speech ended, so back the end time off by that pause.
      const tEnd = Math.max(c.lastEnd + 0.1, now - VAD_SILENCE_S);
      const chunk = { t_start: c.lastEnd, t_end: tEnd, text: t };
      c.lastEnd = tEnd;
      c.lastCommit = now;
      setLastText(t);
      api.addTranscript(sessionId, [chunk]).catch((e) =>
        giveUp(e instanceof ApiError ? e.message : "Could not send the transcript to the server."),
      );
    },
    started: () => {
      retries.current = 0;
      setReconnecting(false);
      setError(null);
    },
    disconnected: () => {
      if (wanted.current) void reconnect();
    },
    error: (e: unknown) => {
      if (isMicError(e)) giveUp(MIC_DENIED);
      // Anything else surfaces as a disconnect, which the reconnect path handles.
    },
    fatal: (message: string) => giveUp(message),
  } as unknown as typeof handlers.current;

  const start = useCallback(async (): Promise<Open> => {
    setError(null);
    wanted.current = true;
    retries.current = 0;
    if (clock.current.t0 === 0) clock.current.t0 = performance.now();
    busy.current = true;
    const r = await open();
    busy.current = false;
    if (r === "ok") {
      setLive(true);
    } else {
      wanted.current = false;
      if (r === "mic") setError(MIC_DENIED);
      else if (r === "connect") setError("Could not connect to ElevenLabs.");
      // r === "token": open() already set the message
    }
    return r;
  }, [open]);

  const stop = useCallback(() => {
    wanted.current = false;
    setLive(false);
    setReconnecting(false);
    // Flush words still waiting for a pause, then close once the final segment has had time to arrive.
    if (scribeRef.current.partialTranscript.trim()) {
      scribeRef.current.commit();
      setTimeout(() => scribeRef.current.disconnect(), 1500);
    } else {
      scribeRef.current.disconnect();
    }
  }, []);

  // Lecture clock, and the safety commit for a lecturer who never pauses.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      const c = clock.current;
      const now = (performance.now() - c.t0) / 1000;
      setElapsed(now);
      if (scribeRef.current.partialTranscript.trim() && now - c.lastCommit > FORCE_COMMIT_AFTER_S) {
        c.lastCommit = now;
        scribeRef.current.commit();
      }
    }, 1000);
    return () => clearInterval(id);
  }, [live]);

  useEffect(
    () => () => {
      wanted.current = false;
      scribeRef.current.disconnect();
    },
    [],
  );

  return { supported: true, live, error, lastText, elapsed, partial: scribe.partialTranscript, reconnecting, start, stop };
}

export type ScribeState = ReturnType<typeof useScribeTranscript>;
