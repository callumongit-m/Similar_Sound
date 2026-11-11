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
  arrangement: { rise_db: number };
  detection: { candidates?: { main: boolean; lead_in_bars: number }[] };
}
export interface Similar { id: string; score: number; title: string }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new Error("Can't reach the Similar Sound service. Is service/run.sh running?");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}
const post = (path: string, body?: unknown) =>
  call<any>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  status: () => call<Status>("/status"),
  addJob: (url: string) => post("/jobs", { url }),
  giveTimestamp: (jobId: number, timestamp: string) => post(`/jobs/${jobId}/timestamp`, { timestamp }),
  dismiss: (jobId: number) => call<any>(`/jobs/${jobId}`, { method: "DELETE" }),
  pause: () => post("/pause"),
  resume: () => post("/resume"),
  tracks: () => call<TrackSummary[]>("/tracks"),
  track: (id: string) => call<TrackDoc>(`/tracks/${id}`),
  similar: (id: string, kind = "groove") => call<Similar[]>(`/tracks/${id}/similar?kind=${kind}`),
  ask: (id: string, question: string) => post(`/tracks/${id}/ask`, { question }) as Promise<{ answer: string }>,
};
