// "Add song": paste a link, watch the song you just added (only the latest one from this session).
import { api, type Job } from "./api";
import { S, trackById } from "./state";
import { rowHtml, songCard, trackChips } from "./songs";
import { $, errorText, esc, niceName, restart, run, syncCards } from "./ui";

const STAGES: Record<string, string> = { download: "Downloading", segment: "Finding the drop", stems: "Splitting stems", features: "Measuring", embed: "Embedding" };

export interface AddHost { onOpen(id: string): void; refresh(): Promise<void> }

export const addSongHtml = `
  <div class="add" id="add"><input id="url" placeholder="Paste a SoundCloud link" spellcheck="false" autocomplete="off" /></div>
  <div class="queue-note" id="queue-note" hidden>Analysis waits while Ableton is open, so it can't glitch your audio. It carries on when you close Ableton.</div>
  <div id="latest"></div>`;

/** Why a job is not moving, in words. The queue holds while Ableton is open (heavy analysis could glitch audio). */
function waitingText(j: Job): string {
  const state = S.status?.state;
  const step = (STAGES[j.stage] ?? "this step").toLowerCase();
  if (j.status === "running") return state === "paused_ableton" ? `Paused at ${step}: Ableton is open` : state === "paused_user" ? `Paused at ${step}` : `${STAGES[j.stage] ?? "Working"}…`;
  if (state === "paused_ableton") return "Waiting for Ableton to close";
  if (state === "paused_user") return "Paused";
  return state === "working" ? "Up next" : "Starting…";
}

function jobCard(j: Job) {
  const track = trackById(j.track_key);
  const title = j.title || track?.title || niceName(j.source);
  const artist = j.artist ?? track?.artist ?? "";
  let meta = "", body = "";
  if (j.status === "running") meta = `<span class="status ${S.status?.state?.startsWith("paused") ? "held" : "running"}">${esc(waitingText(j))}</span>`;
  else if (j.status === "pending") meta = `<span class="status ${S.status?.state === "paused_ableton" ? "held" : ""}">${esc(waitingText(j))}</span>`;
  else if (j.status === "error") { meta = `<span class="status error">Failed</span><button class="x" data-dismiss="${j.id}" title="Dismiss">✕</button>`; body = `<div class="sub wrap err">${esc(j.error)}</div>`; }
  else if (j.status === "needs_timestamp") {
    meta = `<button class="x" data-dismiss="${j.id}" title="Dismiss">✕</button>`;
    body = `<div class="ask-user"><p>${esc(j.error)}</p><div class="row"><input class="field" data-keep="ts-${j.id}" data-ts="${j.id}" placeholder="e.g. 3:04" spellcheck="false" /><button class="btn small" data-send-ts="${j.id}">Continue</button></div></div>`;
  }
  return { key: `j-${j.track_key}`, html: rowHtml(title, artist, meta) + body, cls: "", attrs: { track: undefined } };
}

export function mountAddSong(root: HTMLElement, host: AddHost) {
  const input = $<HTMLInputElement>("#url", root);
  const bar = $("#add", root);

  async function submit() {
    const url = input.value.trim();
    if (!url) return;
    input.value = "";
    restart(bar, "sent");
    await run(async () => { const { id } = await api.addJob(url); S.sessionJobs.add(id); await host.refresh(); });
  }
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  bar.addEventListener("dragover", (e) => { e.preventDefault(); bar.classList.add("dragover"); });
  bar.addEventListener("dragleave", () => bar.classList.remove("dragover"));
  bar.addEventListener("drop", (e) => {
    e.preventDefault();
    bar.classList.remove("dragover");
    const text = e.dataTransfer?.getData("text/uri-list") || e.dataTransfer?.getData("text/plain") || "";
    if (text) { input.value = text.split("\n")[0].trim(); submit(); }
  });

  root.addEventListener("click", (e) => {
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-dismiss],[data-send-ts],.card.click");
    if (!t) return;
    if (t.dataset.dismiss) run(async () => { await api.dismiss(Number(t.dataset.dismiss)); await host.refresh(); });
    else if (t.dataset.sendTs) {
      const id = Number(t.dataset.sendTs);
      const ts = root.querySelector<HTMLInputElement>(`[data-ts="${id}"]`)?.value ?? "";
      run(async () => { await api.giveTimestamp(id, ts); await host.refresh(); });
    } else if (t.dataset.track) host.onOpen(t.dataset.track);
  });
  root.addEventListener("keydown", (e) => {
    const el = e.target as HTMLElement;
    if (e.key === "Enter" && el.dataset.ts) root.querySelector<HTMLElement>(`[data-send-ts="${el.dataset.ts}"]`)?.click();
  });
}

/** Every song added since the window opened, in the order they were added (that is also the order they are analysed):
 *  waiting, in progress, stuck or finished. One card per song, which turns from "analysing" to "analysed" in place. */
export function updateAddSong(root: HTMLElement) {
  const latestPerSong = new Map<string, Job>();
  for (const j of (S.status?.jobs ?? []).filter((j) => S.sessionJobs.has(j.id)).sort((a, b) => a.id - b.id)) latestPerSong.set(j.track_key, j);
  const jobs = [...latestPerSong.values()].sort((a, b) => a.id - b.id);
  const cards = jobs.map((j) => {
    const track = trackById(j.track_key);
    if (j.status === "done" && track) return songCard(track, { keyPrefix: "j" });
    if (j.status === "done") return { key: `j-${j.track_key}`, html: rowHtml(j.title ?? niceName(j.source), j.artist ?? "", `<span class="status">Done</span>`) };
    return jobCard(j);
  });
  syncCards($("#latest", root), cards);
  const held = S.status?.state === "paused_ableton" && jobs.some((j) => j.status === "pending" || j.status === "running");
  $("#queue-note", root).hidden = !held;
}

export { errorText, trackChips };
