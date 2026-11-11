// Typed client for the local Similar_Sound service. The only place that knows URLs and payload shapes.
const BASE = "http://127.0.0.1:8765";

export type JobStatus = "pending" | "running" | "done" | "error" | "needs_timestamp";
export interface Job { id: number; source: string; status: JobStatus; stage: string; error: string | null; title: string | null; artist: string | null; track_key: string }
export type QueueState = "idle" | "working" | "paused_ableton" | "paused_user";
export interface Status { state: QueueState; jobs: Job[] }
export interface TrackSummary { id: string; title: string; artist: string; source: string; bpm: number | null; key: string | null }
export interface TrackDoc extends TrackSummary {
  segments: { build: [number, number]; drop: [number, number] };
  alternative_drops: number[];
  groove: { swing?: number };
}
export interface LlmSummary { configured: boolean; provider: string | null; name: string | null; model: string | null }
export interface Provider { id: string; name: string; needs_address: boolean }
export interface Similar { id: string; score: number; title: string }
export interface Collection { id: number; name: string; track_ids: string[] }
export type ListenState = "idle" | "waiting" | "listening" | "analysing" | "ready" | "error";
export interface Listen { state: ListenState; level: number; seconds: number; message: string; ableton_running: boolean; heard: { bpm: number; key: string | null; seconds: number | null } | null }
export interface Gap { label: string; yours: number; reference: number; delta: number; outside: boolean; n: number; text: string }
export interface Comparison { reference: string; n: number; is_collection: boolean; small: boolean; note: string; references: string[]; gaps: Gap[] }

/** A failure with the sentence to show; `code` lets the UI react (e.g. ask for the Gemini key). */
export class ApiError extends Error {
  constructor(message: string, public code = "error", public extra: any = undefined) { super(message); }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch (e) {
    throw new ApiError(`Can't reach the Similar Sound service (${String(e)}). Is service/run.sh running?`, "offline");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const d = body.detail;
    if (d && typeof d === "object" && "message" in d) throw new ApiError(d.message, d.code, d);
    throw new ApiError(typeof d === "string" ? d : `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}
const post = <T = any>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const del = <T = any>(path: string) => call<T>(path, { method: "DELETE" });

export const api = {
  status: () => call<Status>("/status"),
  addJob: (url: string) => post<{ id: number }>("/jobs", { url }),
  giveTimestamp: (jobId: number, timestamp: string) => post(`/jobs/${jobId}/timestamp`, { timestamp }),
  dismiss: (jobId: number) => del(`/jobs/${jobId}`),
  pause: () => post("/pause"),
  resume: () => post("/resume"),
  tracks: () => call<TrackSummary[]>("/tracks"),
  track: (id: string) => call<TrackDoc>(`/tracks/${id}`),
  similar: (id: string, kind = "groove") => call<Similar[]>(`/tracks/${id}/similar?kind=${kind}`),
  ask: (id: string, question: string) => post<{ answer: string }>(`/tracks/${id}/ask`, { question }),
  // the API key (any company): sent once, stored in the Keychain by the service, never read back
  llm: () => call<LlmSummary>("/settings"),
  saveKey: (body: { key: string; provider?: string; base_url?: string; model?: string }) => post<LlmSummary>("/settings/llm", body),
  removeKey: () => del<LlmSummary>("/settings/llm"),
  // collections
  collections: () => call<Collection[]>("/collections"),
  newCollection: (name: string) => post<Collection>("/collections", { name }),
  renameCollection: (id: number, name: string) => call(`/collections/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deleteCollection: (id: number) => del(`/collections/${id}`),
  addToCollection: (id: number, trackId: string) => post(`/collections/${id}/tracks`, { track_id: trackId }),
  removeFromCollection: (id: number, trackId: string) => del(`/collections/${id}/tracks/${trackId}`),
  // hear the project
  listen: () => call<Listen>("/listen"),
  listenStart: () => post<Listen>("/listen/start"),
  listenCancel: () => post<Listen>("/listen/cancel"),
  compare: (kind: "track" | "collection", id: string | number) => post<Comparison>("/compare", { kind, id }),
  askCompare: (question: string) => post<{ answer: string }>("/compare/ask", { question }),
};
