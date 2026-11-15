// Typed REST client. Types are generated from the backend's Pydantic models (npm run gen:types).
import type { components } from "./api-types";

type S = components["schemas"];
export type CreateSessionResponse = S["CreateSessionResponse"];
export type JoinResponse = S["JoinResponse"];
export type SessionPublic = S["SessionPublic"];
export type DashboardResponse = S["DashboardResponse"];
export type QuestionOut = S["QuestionOut"];
export type FeedbackOut = S["FeedbackOut"];
export type HintOut = S["HintOut"];
export type HeatmapConcept = S["HeatmapConcept"];
export type ModeCounts = S["ModeCounts"];
export type Escalation = S["Escalation"];
export type OpsResponse = S["OpsResponse"];
export type RecapOut = S["RecapOut"];
export type RoomScoreOut = S["RoomScoreOut"];
export type Verdict = S["Verdict"];
export type Health = { status: string; storage: string; llm: string; elevenlabs: boolean };
export type ResolveAction = S["ResolveEscalationRequest"]["action"];
export type Concept = S["Concept"];
export type StudentStatus = S["StudentStatusOut"];
export type ReplayStatus = S["ReplayStatus"];
export type TraceBack = { from_concept: string; to_concept: string; to_concept_id: string; at: string; message: string };
export type Mode = S["Mode"];
export type ErrorCode = S["ErrorCode"];
export type WSMessage = S["WSMessage"];
export type WSType = WSMessage["type"];

// Where the browser reaches the backend (every call is browser -> backend; the frontend has no
// server-side calls to it). NEXT_PUBLIC_API_URL wins when set and non-empty. Otherwise:
//   - a production build uses "/api": on Vercel the backend service is public at /api on the same
//     domain (see vercel.json), so there is no CORS and no second hostname;
//   - `npm run dev` uses the FastAPI dev server on :8000.
// Under `vercel dev`, set NEXT_PUBLIC_API_URL=/api in frontend/.env.local.
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "/api" : "http://localhost:8000")
).replace(/\/$/, "");

/** WebSocket base URL for API_URL. Browser only: a same-origin path like "/api" needs the page's host. */
export function wsBase(): string {
  if (/^https?:\/\//.test(API_URL)) return API_URL.replace(/^http/, "ws");
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}${API_URL}`;
}

/** Largest slide PDF the deployment accepts: Vercel Functions reject request bodies over 4.5 MB. */
export const MAX_SLIDES_BYTES = API_URL.startsWith("/") ? 4_400_000 : 20_000_000;

export class ApiError extends Error {
  constructor(public code: ErrorCode | "network_error", message: string, public status = 0) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("network_error", `Can't reach the LectureLoop server at ${API_URL}.`);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = body?.error;
    throw new ApiError(err?.code ?? "invalid_input", err?.message ?? `Request failed (${res.status})`, res.status);
  }
  return body as T;
}

export const api = {
  health: () => request<Health>("/health"),
  /** A single-use ElevenLabs Scribe token (15 min, consumed on connect). The API key never leaves the server. */
  scribeToken: (id: string) => request<{ token: string }>(`/sessions/${id}/scribe-token`, { cache: "no-store" }),
  createSession: (title: string) =>
    request<CreateSessionResponse>("/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  getSession: (id: string) => request<SessionPublic>(`/sessions/${id}`),
  join: (join_code: string, nickname: string) =>
    request<JoinResponse>("/join", { method: "POST", body: JSON.stringify({ join_code, nickname }) }),
  dashboard: (id: string) => request<DashboardResponse>(`/sessions/${id}/dashboard`),
  startReplay: (id: string, speed: number) =>
    request<ReplayStatus>(`/sessions/${id}/replay`, { method: "POST", body: JSON.stringify({ speed }) }),
  setReplaySpeed: (id: string, speed: number) =>
    request<ReplayStatus>(`/sessions/${id}/replay/speed`, { method: "POST", body: JSON.stringify({ speed }) }),
  pauseReplay: (id: string) => request<ReplayStatus>(`/sessions/${id}/replay/pause`, { method: "POST" }),
  endSession: (id: string) => request<SessionPublic>(`/sessions/${id}/end`, { method: "POST" }),
  escalations: (id: string) => request<Escalation[]>(`/sessions/${id}/escalations`),
  resolveEscalation: (escalationId: string, action: ResolveAction, extra: { edit?: string; verdict?: Verdict } = {}) =>
    request<Escalation>(`/escalations/${escalationId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ action, ...extra }),
    }),
  ops: (id: string) => request<OpsResponse>(`/sessions/${id}/ops`),
  addTranscript: (id: string, chunks: { t_start: number; t_end: number; text: string }[]) =>
    request<{ accepted: number }>(`/sessions/${id}/transcript`, { method: "POST", body: JSON.stringify({ chunks }) }),
  uploadSlides: async (id: string, file: File) => {
    // multipart: let the browser set the Content-Type boundary, so this bypasses request()
    if (file.size > MAX_SLIDES_BYTES) {
      const mb = (n: number) => (n / 1_000_000).toFixed(1);
      throw new ApiError("invalid_input", `That PDF is ${mb(file.size)} MB; the limit here is ${mb(MAX_SLIDES_BYTES)} MB.`);
    }
    const body = new FormData();
    body.append("file", file);
    let res: Response;
    try {
      res = await fetch(`${API_URL}/sessions/${id}/slides`, { method: "POST", body });
    } catch {
      throw new ApiError("network_error", `Can't reach the LectureLoop server at ${API_URL}.`);
    }
    const json = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(json?.error?.code ?? "invalid_input", json?.error?.message ?? "Upload failed", res.status);
    return json as { pages: number };
  },
};
