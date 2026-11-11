// "Add song": paste a link, watch the song you just added (only the latest one from this session).
import { api, type Job } from "./api";
import { S, trackById } from "./state";
import { rowHtml, songCard, trackChips } from "./songs";
import { $, errorText, esc, niceName, restart, run, syncCards } from "./ui";

const STAGES: Record<string, string> = { download: "Downloading", segment: "Finding the drop", stems: "Splitting stems", features: "Measuring", embed: "Embedding" };

export interface AddHost { onOpen(id: string): void; refresh(): Promise<void> }

export const addSongHtml = `
  <div class="add" id="add"><input id="url" placeholder="Paste a SoundCloud link" spellcheck="false" autocomplete="off" /></div>
  <div class="qcontrol" id="qcontrol" hidden>
    <div class="qtext" data-qtext></div>
    <div class="qtoggle" data-qtoggle style="--i:0" role="group" aria-label="Track analysis">
      <span class="slider"></span>
      <button data-act="resume">▶ Play</button><button data-act="pause">❚❚ Pause</button>
    </div>
  </div>
  <div id="latest"></div>`;

const isPaused = () => S.status?.state === "paused_ableton" || S.status?.state === "paused_user";

/** What a song that is not finished says. Anything held by a pause (yours, or the automatic one while Ableton is
 *  open) just says "Paused"; the line under the input says why. */
function waitingText(j: Job): string {
  if (isPaused()) return "Paused";
  if (j.status === "running") return `${STAGES[j.stage] ?? "Working"}…`;
  return S.status?.state === "working" ? "Up next" : "Starting…";
}

function jobCard(j: Job) {
  const track = trackById(j.track_key);
  const title = j.title || track?.title || niceName(j.source);
  const artist = j.artist ?? track?.artist ?? "";
  let meta = "", body = "";
  if (j.status === "running") meta = `<span class="status ${isPaused() ? "held" : "running"}">${esc(waitingText(j))}</span>`;
  else if (j.status === "pending") meta = `<span class="status ${isPaused() ? "held" : ""}">${esc(waitingText(j))}</span>`;
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
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-dismiss],[data-send-ts],[data-act],.card.click");
    if (!t) return;
    if (t.dataset.act) {                       // Play / Pause: Play also runs the queue while Ableton is open
      if ((t.dataset.act === "resume") === isPaused()) run(async () => { await (isPaused() ? api.resume() : api.pause()); await host.refresh(); });
    } else if (t.dataset.dismiss) run(async () => { await api.dismiss(Number(t.dataset.dismiss)); await host.refresh(); });
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

/** The songs on this screen: everything added since the window opened (in the order added, which is also the order
 *  they are analysed) plus anything still waiting in the queue from an earlier session (so queued work never
 *  vanishes after a restart). One card per song, which turns from waiting to analysing to analysed in place. */
export function updateAddSong(root: HTMLElement) {
  const wanted = (j: Job) => S.sessionJobs.has(j.id) || ["pending", "running", "needs_timestamp"].includes(j.status);
  const latestPerSong = new Map<string, Job>();
  for (const j of (S.status?.jobs ?? []).filter(wanted).sort((a, b) => a.id - b.id)) latestPerSong.set(j.track_key, j);
  const jobs = [...latestPerSong.values()].sort((a, b) => a.id - b.id);
  const cards = jobs.map((j) => {
    const track = trackById(j.track_key);
    if (j.status === "done" && track) return songCard(track, { keyPrefix: "j" });
    if (j.status === "done") return { key: `j-${j.track_key}`, html: rowHtml(j.title ?? niceName(j.source), j.artist ?? "", `<span class="status">Done</span>`) };
    return jobCard(j);
  });
  syncCards($("#latest", root), cards);
  updateQueueControl(root, jobs.some((j) => j.status === "pending" || j.status === "running"));
}

/** The line under the input: whether analysis is running, and the Play/Pause toggle. Shown whenever there is
 *  work in the queue or it is paused, so there is always a way to resume. */
function updateQueueControl(root: HTMLElement, hasWork: boolean) {
  const state = S.status?.state;
  const box = $("#qcontrol", root);
  box.hidden = !(hasWork || isPaused());
  $("[data-qtext]", box).textContent =
    state === "paused_ableton" ? "Track analysis is paused whilst Ableton is open, you can resume the analysis using the toggle"
    : state === "paused_user" ? "Track analysis is paused, you can resume the analysis using the toggle"
    : "Track analysis is running, you can pause it using the toggle";
  const toggle = $("[data-qtoggle]", box);
  toggle.style.setProperty("--i", isPaused() ? "1" : "0");
  toggle.querySelectorAll<HTMLElement>("[data-act]").forEach((b) => b.classList.toggle("active", (b.dataset.act === "pause") === isPaused()));
}

export { errorText, trackChips };
