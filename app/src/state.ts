// Shared, read-mostly state. main.ts refreshes it; each view re-renders itself from it.
import type { Collection, Job, Listen, LlmSummary, Status, TrackSummary } from "./api";

export const S = {
  status: null as Status | null,
  tracks: [] as TrackSummary[],
  collections: [] as Collection[],
  offline: false,
  booting: true,                       // the service is still starting: show a ghost of the window, not an error
  sessionJobs: new Set<number>(),      // jobs started since this window opened ("Add song" shows only these)
  listen: null as Listen | null,
  llm: null as LlmSummary | null,        // which company's key is connected (never the key itself)
};

export const trackById = (id: string) => S.tracks.find((t) => t.id === id);
export const openJobs = (): Job[] => (S.status?.jobs ?? []).filter((j) => j.status !== "done");

/** How long to wait for the service before calling it "not running" (it takes about 10 to 20 seconds to start). */
export const BOOT_GRACE_MS = 30_000;
