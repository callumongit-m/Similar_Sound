// Shared, read-mostly state. main.ts refreshes it; each view re-renders itself from it.
import type { Collection, Job, Listen, Status, TrackSummary } from "./api";

export const S = {
  status: null as Status | null,
  tracks: [] as TrackSummary[],
  collections: [] as Collection[],
  offline: false,
  sessionJobs: new Set<number>(),      // jobs started since this window opened ("Add song" shows only these)
  listen: null as Listen | null,
};

export const trackById = (id: string) => S.tracks.find((t) => t.id === id);
export const openJobs = (): Job[] => (S.status?.jobs ?? []).filter((j) => j.status !== "done");
