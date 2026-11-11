import { LogicalSize } from "@tauri-apps/api/dpi";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { api, type Job, type Similar, type Status, type TrackDoc, type TrackSummary } from "./api";

// ---------- state ----------
let status: Status | null = null;
let tracks: TrackSummary[] = [];
let detail: { doc: TrackDoc; similar: Similar[] } | null = null;
let answer: { text: string; err: boolean } | null = null;
let offline = false;
let toastTimer = 0;

// ---------- helpers ----------
const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const STAGES: Record<string, string> = { download: "Downloading", segment: "Finding the drop", stems: "Splitting stems", features: "Measuring", embed: "Embedding" };
const run = async (fn: () => Promise<unknown>) => { try { await fn(); } catch (e) { toast(e); } };
const restart = (el: HTMLElement, cls: string) => { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); };

/** Name guessed from the URL, used only until the real title arrives (about a second). */
function niceName(source: string): string {
  const base = source.split("#")[0].split("?")[0];
  const seg = base.split("/").filter(Boolean).pop() ?? base;
  if (/^on\.soundcloud\.com/.test(base)) return "SoundCloud link";
  return decodeURIComponent(seg).replace(/-\d+$/, "").replace(/[-_]+/g, " "); // "-5" is SoundCloud's slug suffix
}

// ---------- items: one entry per track, whether it is still being analysed or done ----------
interface Item { key: string; title: string; artist: string; job?: Job; track?: TrackSummary }

function buildItems(): Item[] {
  const byKey = new Map(tracks.map((t) => [t.id, t]));
  const latest = new Map<string, Job>();
  for (const j of status?.jobs ?? []) if (j.status !== "done" && !latest.has(j.track_key)) latest.set(j.track_key, j); // jobs arrive newest first
  const items: Item[] = [];
  for (const [key, job] of latest) {
    const track = byKey.get(key);
    items.push({ key, job, track, title: job.title || track?.title || niceName(job.source), artist: job.artist ?? track?.artist ?? "" });
  }
  for (const t of tracks) if (!latest.has(t.id)) items.push({ key: t.id, track: t, title: t.title || niceName(t.source), artist: t.artist });
  return items;
}

const chip = (text: string, i: number) => `<span class="chip pop" style="--i:${i}">${esc(text)}</span>`;

function cardHtml(it: Item): string {
  const j = it.job;
  let meta = "";
  let body = "";
  if (j?.status === "running") meta = `<span class="status running">${esc(STAGES[j.stage] ?? "Working")}…</span>`;
  else if (j?.status === "pending") meta = `<span class="status">Waiting</span>`;
  else if (j?.status === "error") meta = `<span class="status error">Failed</span><button class="x" data-dismiss="${j.id}" title="Dismiss">✕</button>`;
  else if (j?.status === "needs_timestamp") meta = `<button class="x" data-dismiss="${j.id}" title="Dismiss">✕</button>`;
  else if (it.track) {
    const t = it.track;
    meta = [t.bpm ? chip(`${Math.round(t.bpm)} bpm`, 0) : "", t.key ? chip(t.key, 1) : ""].join("");
  }
  if (j?.status === "needs_timestamp")
    body = `<div class="ask-user"><p>${esc(j.error)}</p><div class="row"><input class="field" data-keep="ts-${j.id}" data-ts="${j.id}" placeholder="e.g. 3:04" spellcheck="false" /><button class="btn small" data-send-ts="${j.id}">Continue</button></div></div>`;
  else if (j?.status === "error") body = `<div class="sub wrap err">${esc(j.error)}</div>`;
  return `<div class="row"><div class="grow"><div class="title">${esc(it.title)}</div><div class="sub">${esc(it.artist)}</div></div><div class="meta">${meta}</div></div>${body}`;
}

// ---------- keyed list: only genuinely new cards animate in; removed ones animate out ----------
type CardEl = HTMLElement & { _html?: string };

function syncList(items: Item[]) {
  const list = $("#list");
  const existing = new Map<string, CardEl>();
  list.querySelectorAll<CardEl>(":scope > .card").forEach((el) => { if (!el.classList.contains("leaving")) existing.set(el.dataset.key!, el); });
  const wanted = new Set(items.map((i) => i.key));
  for (const [key, el] of existing) {
    if (wanted.has(key)) continue;
    el.classList.add("leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 500); // safety net if animations are throttled
  }
  let cursor: Element | null = list.firstElementChild;
  for (const it of items) {
    let el = existing.get(it.key);
    if (!el) {
      el = document.createElement("div") as CardEl;
      el.className = "card enter";
      el.dataset.key = it.key;
      el.addEventListener("animationend", (e) => { if (e.target === el) el!.classList.remove("enter"); });
    }
    const html = cardHtml(it);
    if (el._html !== html) {
      const kept = new Map<string, string>();
      el.querySelectorAll<HTMLInputElement>("[data-keep]").forEach((i) => kept.set(i.dataset.keep!, i.value));
      const focus = (document.activeElement as HTMLElement | null)?.dataset?.keep;
      el.innerHTML = html;
      el._html = html;
      el.querySelectorAll<HTMLInputElement>("[data-keep]").forEach((i) => { if (kept.has(i.dataset.keep!)) i.value = kept.get(i.dataset.keep!)!; });
      if (focus) el.querySelector<HTMLElement>(`[data-keep="${focus}"]`)?.focus();
    }
    el.classList.toggle("click", !!it.track && !it.job);
    el.dataset.track = it.track && !it.job ? it.track.id : "";
    while (cursor && (cursor as HTMLElement).classList.contains("leaving")) cursor = cursor.nextElementSibling;
    if (cursor === el) cursor = el.nextElementSibling;
    else list.insertBefore(el, cursor);
  }
}

// ---------- detail ----------
function detailHtml(d: TrackDoc, similar: Similar[]): string {
  const swing = d.groove?.swing;
  const chips: [string, string][] = [];
  if (d.bpm) chips.push(["BPM", String(Math.round(d.bpm))]);
  if (d.key) chips.push(["Key", d.key]);
  if (swing !== undefined) chips.push(["Swing", `${swing >= 0 ? "+" : "−"}${Math.abs(Math.round(swing * 100))}%`]);
  const alts = d.alternative_drops?.length
    ? `<div class="rise" style="--i:4"><div class="section">Not the right drop?</div><div class="drops">${d.alternative_drops.map((t) => `<button class="btn ghost small" data-redrop="${t}">${mmss(t)}</button>`).join("")}</div></div>` : "";
  const sim = similar.length
    ? `<div class="rise" style="--i:5"><div class="section">Closest groove</div>${similar.map((s) => `<div class="card click" data-open="${esc(s.id)}"><div class="row"><div class="grow title">${esc(s.title || s.id)}</div><span class="status">${Math.round(s.score * 100)}%</span></div></div>`).join("")}</div>` : "";
  return `<button class="back" data-back>← Library</button>
    <div class="rise" style="--i:0"><div class="h1">${esc(d.title)}</div><div class="sub">${esc(d.artist)}</div></div>
    <div class="chips">${chips.map(([k, v], i) => `<div class="chip big pop" style="--i:${i + 1}"><span>${esc(k)}</span>${esc(v)}</div>`).join("")}</div>
    <div class="rise" style="--i:3"><div class="section">Ask about this track</div>
    <div class="askbox"><textarea id="q" data-keep="q" placeholder="e.g. What makes the groove feel the way it does?"></textarea>
    <div style="margin-top:8px"><button class="btn small" data-ask>Ask</button></div></div>
    <div id="answer"></div></div>${alts}${sim}`;
}

function renderAnswer() {
  const el = document.querySelector<HTMLElement>("#answer");
  if (!el) return;
  el.innerHTML = answer ? `<div class="answer ${answer.err ? "err" : ""} rise">${esc(answer.text)}</div>` : "";
}

function showDetail() {
  const el = $("#detail");
  el.innerHTML = detailHtml(detail!.doc, detail!.similar);
  answer = null;
  $("#list").hidden = true;
  el.hidden = false;
  restart(el, "slide-in");
  $("#view").scrollTop = 0;
}
function hideDetail() {
  detail = null;
  answer = null;
  $("#detail").hidden = true;
  const list = $("#list");
  list.hidden = false;
  restart(list, "fade-in");
}

// ---------- window: height follows content, eased ----------
const win = (() => { try { return getCurrentWindow(); } catch { return null; } })();
const MIN_H = 96, MAX_H = 640;
let curH = MIN_H, targetH = MIN_H, raf = 0;
function stepHeight() {
  curH += (targetH - curH) * 0.24;
  if (Math.abs(targetH - curH) < 0.6) { curH = targetH; raf = 0; } else raf = requestAnimationFrame(stepHeight);
  win?.setSize(new LogicalSize(400, Math.round(curH)));
}
function fitWindow() {
  const content = $("#content");
  const has = content.offsetHeight > 0;
  const chrome = $(".titlebar").offsetHeight + $("#add").offsetHeight;
  const want = Math.min(MAX_H, Math.max(MIN_H, has ? chrome + content.offsetHeight : chrome));
  if (Math.abs(want - targetH) < 1) return;
  targetH = want;
  if (!raf) raf = requestAnimationFrame(stepHeight);
}
new ResizeObserver(fitWindow).observe($("#content"));

// ---------- toast ----------
function toast(e: unknown) {
  const el = $("#toast");
  el.textContent = e instanceof Error ? e.message : String(e);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove("show"), 5000);
}

// ---------- render ----------
function render() {
  const items = buildItems();
  const queued = (status?.jobs ?? []).filter((j) => j.status !== "done").length;
  const busy = queued > 0 || status?.state === "paused_user";
  $("#pause").classList.toggle("gone", !busy);
  $("#pause").textContent = status?.state === "paused_user" ? "▶" : "❚❚";
  $("#offline").hidden = !offline;
  syncList(items);
}

// ---------- shell ----------
$("#app").innerHTML = `
  <div class="titlebar" data-tauri-drag-region>
    <span class="grabber" data-tauri-drag-region></span>
    <span class="spacer" data-tauri-drag-region></span>
    <button class="iconbtn gone" id="pause" title="Pause / resume the queue">❚❚</button>
    <button class="iconbtn" id="close" title="Hide (the ♪ in the menu bar brings it back)">✕</button>
  </div>
  <div class="add" id="add"><input id="url" placeholder="Paste a SoundCloud link" spellcheck="false" autocomplete="off" /></div>
  <div id="view"><div id="content">
    <div class="hint" id="offline" hidden><b>Service not running</b>Start it with <code>service/run.sh</code>. This window reconnects by itself.</div>
    <div id="list"></div><div id="detail" hidden></div>
  </div></div>
  <div id="toast"></div>`;

$("#close").onclick = () => win?.close(); // the Rust side hides instead of quitting
$("#pause").onclick = () => run(async () => { await (status?.state === "paused_user" ? api.resume() : api.pause()); await refresh(true); });

const urlInput = $<HTMLInputElement>("#url");
async function submitUrl() {
  const url = urlInput.value.trim();
  if (!url) return;
  urlInput.value = "";
  restart($("#add"), "sent");
  if (detail) hideDetail();
  await run(async () => { await api.addJob(url); await refresh(true); });
}
urlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") submitUrl(); });
const addBar = $("#add");
addBar.addEventListener("dragover", (e) => { e.preventDefault(); addBar.classList.add("dragover"); });
addBar.addEventListener("dragleave", () => addBar.classList.remove("dragover"));
addBar.addEventListener("drop", (e) => {
  e.preventDefault();
  addBar.classList.remove("dragover");
  const text = e.dataTransfer?.getData("text/uri-list") || e.dataTransfer?.getData("text/plain") || "";
  if (text) { urlInput.value = text.split("\n")[0].trim(); submitUrl(); }
});

// ---------- actions (event delegation) ----------
async function openTrack(id: string) {
  await run(async () => {
    const [doc, similar] = await Promise.all([api.track(id), api.similar(id).catch(() => [] as Similar[])]);
    detail = { doc, similar };
    showDetail();
  });
}

$("#view").addEventListener("click", (e) => {
  const t = (e.target as HTMLElement).closest<HTMLElement>("[data-dismiss],[data-send-ts],[data-back],[data-redrop],[data-ask],[data-open],.card.click");
  if (!t) return;
  if (t.dataset.dismiss) run(async () => { await api.dismiss(Number(t.dataset.dismiss)); await refresh(true); });
  else if (t.dataset.sendTs) {
    const id = Number(t.dataset.sendTs);
    const input = $("#view").querySelector<HTMLInputElement>(`[data-ts="${id}"]`);
    run(async () => { await api.giveTimestamp(id, input?.value ?? ""); await refresh(true); });
  } else if (t.dataset.back !== undefined) hideDetail();
  else if (t.dataset.redrop && detail) {
    const src = detail.doc.source;
    run(async () => { await api.addJob(`${src}#t=${Number(t.dataset.redrop).toFixed(1)}`); hideDetail(); await refresh(true); });
  } else if (t.dataset.ask !== undefined && detail) {
    const q = $("#view").querySelector<HTMLTextAreaElement>("#q")?.value.trim();
    if (!q) return;
    answer = { text: "Thinking…", err: false };
    renderAnswer();
    api.ask(detail.doc.id, q)
      .then((r) => (answer = { text: r.answer, err: false }))
      .catch((err) => (answer = { text: err instanceof Error ? err.message : String(err), err: true }))
      .finally(renderAnswer);
  } else if (t.dataset.open) openTrack(t.dataset.open);
  else if (t.classList.contains("click") && t.dataset.track) openTrack(t.dataset.track);
});
$("#view").addEventListener("keydown", (e) => {
  const el = e.target as HTMLElement;
  if (e.key === "Enter" && el.dataset.ts) $("#view").querySelector<HTMLElement>(`[data-send-ts="${el.dataset.ts}"]`)?.click();
});

// ---------- polling ----------
let doneCount = -1;
async function refresh(force = false) {
  try {
    status = await api.status();
    offline = false;
    const done = status.jobs.filter((j) => j.status === "done").length;
    if (done !== doneCount || force) { doneCount = done; tracks = await api.tracks(); }
  } catch {
    status = null;
    offline = true;
  }
  render();
}
refresh(true);
setInterval(() => refresh(), 1200);
