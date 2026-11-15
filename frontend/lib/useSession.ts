"use client";
// Native WebSocket client with auto-reconnect. Every message is {type, payload, ts}.
import { useCallback, useEffect, useRef, useState } from "react";
import { WS_URL, type WSMessage, type WSType } from "./api";

export type ConnStatus = "connecting" | "connected" | "reconnecting" | "closed" | "error";

type Handler = (msg: WSMessage) => void;

export function useSession(path: string | null, onMessage: Handler) {
  const [status, setStatus] = useState<ConnStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    if (!path) return;
    let stopped = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const open = () => {
      const ws = new WebSocket(`${WS_URL}${path}`);
      wsRef.current = ws;
      setStatus(retry === 0 ? "connecting" : "reconnecting");
      ws.onopen = () => {
        retry = 0;
        setStatus("connected");
      };
      ws.onmessage = (ev) => {
        try {
          handlerRef.current(JSON.parse(ev.data) as WSMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = (ev) => {
        wsRef.current = null;
        if (stopped) return;
        if (ev.code === 4404) {
          setStatus("error"); // unknown student/session: don't retry
          return;
        }
        retry += 1;
        setStatus("reconnecting");
        timer = setTimeout(open, Math.min(1000 * 2 ** (retry - 1), 8000));
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(timer);
      wsRef.current?.close();
      setStatus("closed");
    };
  }, [path]);

  const send = useCallback((type: WSType, payload: Record<string, unknown> = {}) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type, payload, ts: Date.now() / 1000 }));
    return true;
  }, []);

  return { status, send };
}
