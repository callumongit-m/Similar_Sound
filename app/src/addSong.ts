// "Add song": paste a link, watch the song you just added (only the latest one from this session).
import { api, type Job } from "./api";
import { S, trackById } from "./state";
import { rowHtml, songCard, trackChips } from "./songs";
import { $, errorText, esc, niceName, restart, run, syncCards } from "./ui";

const STAGES: Record<string, string> = { download: "Downloading", segment: "Finding the drop", stems: "Splitting stems", features: "Measuring", embed: "Embedding" };

export interface AddHost { onOpen(id: string): void; refresh(): Promise<void> }

export const addSongHtml = `
  <div class="add" id="add"><input id="url" placeholder="Paste a SoundCloud link" spellcheck="false" autocomplete="off" /></div>
  <div id="latest"></div>`;

function jobCard(j: Job) {
  const track = trackById(j.track_key);
  const title = j.title || track?.title || niceName(j.source);
  const artist = j.artist ?? track?.artist ?? "";
  let meta = "", body = "";
  if (j.status === "running") meta = `<span class="status running">${esc(STAGES[j.stage] ?? "Working")}…</span>`;
  else if (j.status === "pending") meta = `<span class="status">Waiting</span>`;
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

/** The latest song added in this session: still being analysed, stuck, or finished. */
export function updateAddSong(root: HTMLElement) {
  const jobs = (S.status?.jobs ?? []).filter((j) => S.sessionJobs.has(j.id));
  const latest = jobs.sort((a, b) => b.id - a.id)[0];
  const cards = [];
  if (latest) {
    const track = trackById(latest.track_key);
    if (latest.status === "done" && track) cards.push(songCard(track, { keyPrefix: "j" }));
    else if (latest.status === "done") cards.push({ key: `j-${latest.track_key}`, html: rowHtml(latest.title ?? niceName(latest.source), latest.artist ?? "", `<span class="status">Done</span>`) });
    else cards.push(jobCard(latest));
  }
  syncCards($("#latest", root), cards);
}

export { errorText, trackChips };
