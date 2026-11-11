import { getCurrentWindow } from "@tauri-apps/api/window";
import { api, type Job, type QueueState, type Similar, type Status, type TrackDoc, type TrackSummary } from "./api";

// ---------- state ----------
type Tab = "queue" | "library";
let tab: Tab = "queue";
let status: Status | null = null;
let tracks: TrackSummary[] = [];
let detail: { doc: TrackDoc; similar: Similar[] } | null = null;
let answer: { text: string; err: boolean } | null = null;
let pinned = true;
let toastMsg = "";
let lastSig = "";
let offline = false;

// ---------- helpers ----------
const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const STAGES: Record<string, string> = { download: "Downloading", segment: "Finding the drop", stems: "Splitting stems", features: "Measuring", embed: "Embedding" };
const STATE_LABEL: Record<QueueState, string> = { idle: "Idle", working: "Working", paused_ableton: "Paused: Ableton open", paused_user: "Paused" };

function niceName(source: string): string {
  const base = source.split("#")[0];
  const seg = base.split("/").filter(Boolean).pop() ?? base;
  if (/^on\.soundcloud\.com/.test(base) || /^[A-Za-z0-9]{12,}$/.test(seg)) return "SoundCloud link";
  return decodeURIComponent(seg.split("?")[0]).replace(/[-_]+/g, " ");
}
function trackTitle(id: string | null): string | null {
  return tracks.find((t) => t.id === id)?.title || null;
}
function showError(e: unknown) {
  toastMsg = e instanceof Error ? e.message : String(e);
  renderToast();
  setTimeout(() => { toastMsg = ""; renderToast(); }, 5000);
}
const run = async (fn: () => Promise<unknown>) => { try { await fn(); } catch (e) { showError(e); } };

// ---------- shell ----------
$("#app").innerHTML = `
  <div class="titlebar" data-tauri-drag-region>
    <span class="name" data-tauri-drag-region>Similar Sound</span>
    <span class="pill" id="pill" data-tauri-drag-region><i></i><span id="pilltext">Connecting</span></span>
    <button class="iconbtn" id="pause" title="Pause / resume the queue">❚❚</button>
    <button class="iconbtn on" id="pin" title="Keep on top">⌖</button>
    <button class="iconbtn" id="min" title="Minimise">–</button>
    <button class="iconbtn" id="close" title="Close">×</button>
  </div>
  <div class="add" id="add">
    <input id="url" placeholder="Paste a SoundCloud link and press Enter" spellcheck="false" autocomplete="off" />
    <button class="btn" id="addbtn">Add</button>
  </div>
  <div class="tabs">
    <button class="tab" data-tab="queue">Queue<span class="count" id="qcount"></span></button>
    <button class="tab" data-tab="library">Library<span class="count" id="lcount"></span></button>
  </div>
  <div id="view"></div>
  <div id="toast"></div>`;

const win = (() => { try { return getCurrentWindow(); } catch { return null; } })();
$("#close").onclick = () => win?.close();
$("#min").onclick = () => win?.minimize();
$("#pin").onclick = async () => {
  pinned = !pinned;
  await win?.setAlwaysOnTop(pinned);
  $("#pin").classList.toggle("on", pinned);
};
$("#pause").onclick = () => run(async () => {
  const paused = status?.state === "paused_user";
  await (paused ? api.resume() : api.pause());
  await refreshStatus(true);
});
document.querySelectorAll<HTMLButtonElement>(".tab").forEach((b) => (b.onclick = () => { tab = b.dataset.tab as Tab; detail = null; answer = null; render(true); }));

const urlInput = $<HTMLInputElement>("#url");
async function submitUrl() {
  const url = urlInput.value.trim();
  if (!url) return;
  urlInput.value = "";
  await run(async () => { await api.addJob(url); tab = "queue"; detail = null; await refreshStatus(true); });
}
urlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") submitUrl(); });
$("#addbtn").onclick = submitUrl;
const addBar = $("#add");
addBar.addEventListener("dragover", (e) => { e.preventDefault(); addBar.classList.add("dragover"); });
addBar.addEventListener("dragleave", () => addBar.classList.remove("dragover"));
addBar.addEventListener("drop", (e) => {
  e.preventDefault();
  addBar.classList.remove("dragover");
  const text = e.dataTransfer?.getData("text/uri-list") || e.dataTransfer?.getData("text/plain") || "";
  if (text) { urlInput.value = text.split("\n")[0].trim(); submitUrl(); }
});

// ---------- views ----------
function jobCard(j: Job): string {
  const title = trackTitle(j.track_id) ?? niceName(j.source);
  const clickable = j.status === "done" && j.track_id;
  let statusHtml = "";
  if (j.status === "pending") statusHtml = `<span class="status">Waiting</span>`;
  else if (j.status === "running") statusHtml = `<span class="status running">${esc(STAGES[j.stage] ?? "Working")}…</span>`;
  else if (j.status === "done") statusHtml = `<span class="status">Done</span>`;
  else if (j.status === "error") statusHtml = `<span class="status error">Failed</span>`;
  else statusHtml = `<span class="status needs">Needs a timestamp</span>`;
  const body =
    j.status === "needs_timestamp"
      ? `<div class="ask-user"><p>${esc(j.error)}</p>
           <div class="row"><input data-keep="ts-${j.id}" data-ts="${j.id}" placeholder="e.g. 3:04" spellcheck="false" /><button class="btn small" data-send-ts="${j.id}">Continue</button></div></div>`
      : j.status === "error"
        ? `<div class="sub" style="white-space:normal;color:var(--bad)">${esc(j.error)}</div>` : "";
  return `<div class="card ${clickable ? "click" : ""}" ${clickable ? `data-open="${esc(j.track_id)}"` : ""}>
    <div class="row"><div class="grow"><div class="title">${esc(title)}</div></div>${statusHtml}</div>${body}</div>`;
}

function queueView(): string {
  if (offline) return `<div class="empty"><b>Service not running</b>Start it with <code>service/run.sh</code>, then this window reconnects by itself.</div>`;
  const jobs = status?.jobs ?? [];
  if (!jobs.length) return `<div class="empty"><b>Nothing queued</b>Paste a link above. It keeps working in the background, and you can add tracks while Ableton is open.</div>`;
  return jobs.map(jobCard).join("");
}

function libraryView(): string {
  if (detail) return detailView(detail.doc, detail.similar);
  if (!tracks.length) return `<div class="empty"><b>No tracks yet</b>Finished tracks show up here.</div>`;
  return tracks.map((t) => `<div class="card click" data-open="${esc(t.id)}">
    <div class="title">${esc(t.title || niceName(t.source))}</div>
    <div class="sub">${[t.artist, t.bpm ? `${Math.round(t.bpm)} bpm` : "", t.key].filter(Boolean).map(esc).join(" · ")}</div></div>`).join("");
}

function detailView(d: TrackDoc, similar: Similar[]): string {
  const lead = d.detection?.candidates?.find((c) => c.main)?.lead_in_bars;
  const chips = [
    d.bpm ? ["BPM", Math.round(d.bpm)] : null,
    d.key ? ["Key", d.key] : null,
    ["Drop", mmss(d.segments.drop[0])],
    lead ? ["Build-up", `${lead} bars`] : null,
    d.groove?.swing !== undefined ? ["Swing", `${d.groove.swing >= 0 ? "+" : ""}${(d.groove.swing * 100).toFixed(0)}%`] : null,
  ].filter(Boolean) as [string, string | number][];
  const alts = d.alternative_drops?.length
    ? `<div class="section">Not the right drop?</div><div class="drops">${d.alternative_drops
        .map((t) => `<button class="btn ghost small" data-redrop="${t}">${mmss(t)}</button>`).join("")}</div>` : "";
  const sim = similar.length
    ? `<div class="section">Closest groove</div>${similar.map((s) => `<div class="card click" data-open="${esc(s.id)}"><div class="row"><div class="grow title">${esc(s.title || s.id)}</div><span class="status">${Math.round(s.score * 100)}%</span></div></div>`).join("")}` : "";
  return `<button class="back" data-back>← Library</button>
    <div class="h1">${esc(d.title || niceName(d.source))}</div><div class="sub">${esc(d.artist)}</div>
    <div class="chips">${chips.map(([k, v]) => `<div class="chip"><span>${esc(k)}</span>${esc(v)}</div>`).join("")}</div>
    <div class="section">Ask about this track</div>
    <div class="askbox"><textarea id="q" data-keep="q" placeholder="e.g. What makes the groove feel the way it does?"></textarea>
    <div style="margin-top:8px"><button class="btn small" data-ask>Ask</button></div></div>
    ${answer ? `<div class="answer ${answer.err ? "err" : ""}">${esc(answer.text)}</div>` : ""}
    ${alts}${sim}`;
}

// ---------- render ----------
function renderToast() {
  $("#toast").innerHTML = toastMsg ? `<div class="toast">${esc(toastMsg)}</div>` : "";
}

function render(force = false) {
  const state = status?.state ?? "idle";
  const pill = $("#pill");
  pill.className = "pill " + (state === "working" ? "working" : state.startsWith("paused") ? "paused" : "");
  $("#pilltext").textContent = status ? STATE_LABEL[state] : "Connecting";
  $("#pause").textContent = state === "paused_user" ? "▶" : "❚❚";
  document.querySelectorAll<HTMLElement>(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  const open = (status?.jobs ?? []).filter((j) => j.status !== "done").length;
  $("#qcount").textContent = open ? String(open) : "";
  $("#lcount").textContent = tracks.length ? String(tracks.length) : "";

  const sig = JSON.stringify([tab, offline, status?.jobs, tracks.map((t) => t.id + t.title), detail?.doc.id, answer]);
  if (!force && sig === lastSig) return;
  lastSig = sig;

  const view = $("#view");
  const scroll = view.scrollTop;
  const kept = new Map<string, string>();
  view.querySelectorAll<HTMLInputElement>("[data-keep]").forEach((el) => kept.set(el.dataset.keep!, el.value));
  const focusKey = (document.activeElement as HTMLElement | null)?.dataset?.keep;
  view.innerHTML = tab === "queue" ? queueView() : libraryView();
  view.querySelectorAll<HTMLInputElement>("[data-keep]").forEach((el) => { if (kept.has(el.dataset.keep!)) el.value = kept.get(el.dataset.keep!)!; });
  if (focusKey) view.querySelector<HTMLElement>(`[data-keep="${focusKey}"]`)?.focus();
  view.scrollTop = scroll;
}

// ---------- actions (event delegation) ----------
async function openTrack(id: string) {
  await run(async () => {
    const [doc, similar] = await Promise.all([api.track(id), api.similar(id).catch(() => [] as Similar[])]);
    detail = { doc, similar };
    answer = null;
    tab = "library";
    render(true);
    $("#view").scrollTop = 0;
  });
}

$("#view").addEventListener("click", (e) => {
  const t = (e.target as HTMLElement).closest<HTMLElement>("[data-open],[data-back],[data-send-ts],[data-redrop],[data-ask]");
  if (!t) return;
  if (t.dataset.back !== undefined) { detail = null; answer = null; render(true); }
  else if (t.dataset.open) openTrack(t.dataset.open);
  else if (t.dataset.sendTs) {
    const id = Number(t.dataset.sendTs);
    const input = $("#view").querySelector<HTMLInputElement>(`[data-ts="${id}"]`);
    run(async () => { await api.giveTimestamp(id, input?.value ?? ""); await refreshStatus(true); });
  } else if (t.dataset.redrop && detail) {
    const src = detail.doc.source;
    run(async () => { await api.addJob(`${src}#t=${Number(t.dataset.redrop).toFixed(1)}`); tab = "queue"; detail = null; await refreshStatus(true); });
  } else if (t.dataset.ask !== undefined && detail) {
    const q = $("#view").querySelector<HTMLTextAreaElement>("#q")?.value.trim();
    if (!q) return;
    answer = { text: "Thinking…", err: false };
    render(true);
    api.ask(detail.doc.id, q)
      .then((r) => (answer = { text: r.answer, err: false }))
      .catch((err) => (answer = { text: err instanceof Error ? err.message : String(err), err: true }))
      .finally(() => render(true));
  }
});
$("#view").addEventListener("keydown", (e) => {
  const el = e.target as HTMLElement;
  if (e.key === "Enter" && el.dataset.ts) $("#view").querySelector<HTMLElement>(`[data-send-ts="${el.dataset.ts}"]`)?.click();
});

// ---------- polling ----------
let doneCount = -1;
async function refreshStatus(force = false) {
  try {
    status = await api.status();
    offline = false;
    const done = status.jobs.filter((j) => j.status === "done").length;
    if (done !== doneCount || force) { doneCount = done; tracks = await api.tracks(); }
  } catch {
    status = null;
    offline = true;
  }
  render(force);
}
refreshStatus(true);
setInterval(() => refreshStatus(), 1500);
