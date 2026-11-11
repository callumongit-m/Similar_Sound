import { invoke } from "@tauri-apps/api/core";
import { LogicalSize } from "@tauri-apps/api/dpi";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { addSongHtml, mountAddSong, updateAddSong } from "./addSong";
import { api } from "./api";
import { collectionsHtml, mountCollections, updateCollections } from "./collections";
import { compareHtml, enterCompare, leaveCompare, mountCompare } from "./compare";
import { openDetail } from "./detail";
import { keyPanelHtml, mountKeyPanel, toggleKeyPanel } from "./keypanel";
import { S } from "./state";
import { $, run } from "./ui";

// ---------- diagnostics: errors and startup go to ~/.similar_sound/app.log (via the Rust side) ----------
const report = (msg: string) => { invoke("log", { msg }).catch(() => {}); };
window.addEventListener("error", (e) => report(`error: ${e.message} @ ${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => report(`unhandled rejection: ${String((e as PromiseRejectionEvent).reason)}`));
report(`page started (${location.href})`);

// ---------- shell ----------
type Tab = "add" | "collections" | "compare";
const TABS: [Tab, string][] = [["add", "Add song"], ["collections", "Collections"], ["compare", "Compare my project"]];
let tab: Tab = "add";
const KEY_ICON = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4.2"/><path d="M11 12l9-9M16.5 6.5l2.5 2.5M14 9l2 2"/></svg>`;

$("#app").innerHTML = `
  <div class="titlebar" data-tauri-drag-region>
    <button class="iconbtn corner keybtn" id="keybtn" title="API key">${KEY_ICON}</button>
    <span class="grabber" data-tauri-drag-region></span>
    <span class="spacer" data-tauri-drag-region></span>
    <button class="iconbtn gone" id="pause" title="Pause / resume the queue">❚❚</button>
    <button class="iconbtn corner" id="close" title="Hide (the ♪ in the menu bar brings it back)">✕</button>
  </div>
  <div class="nav" id="nav" style="--i:0"><span class="slider"></span>${TABS.map(([k, label]) => `<button data-tab="${k}">${label}</button>`).join("")}</div>
  <div id="view"><div id="content">
    ${keyPanelHtml}
    <div class="hint" id="offline" hidden><b>Service not running</b>Start it with <code>service/run.sh</code>. This window reconnects by itself.</div>
    <section id="pane-add" class="pane">${addSongHtml}</section>
    <section id="pane-collections" class="pane" hidden>${collectionsHtml}</section>
    <section id="pane-compare" class="pane" hidden>${compareHtml}</section>
    <section id="detail" class="pane" hidden></section>
  </div></div>
  <div id="toast"></div>`;

const win = (() => { try { return getCurrentWindow(); } catch { return null; } })();
const pane = (t: Tab) => $<HTMLElement>(`#pane-${t}`);

// ---------- window: height follows content, eased ----------
const MIN_H = 120, MAX_H = 660;
let curH = 96, targetH = 96, raf = 0;
function stepHeight() {
  curH += (targetH - curH) * 0.24;
  if (Math.abs(targetH - curH) < 0.6) { curH = targetH; raf = 0; } else raf = requestAnimationFrame(stepHeight);
  win?.setSize(new LogicalSize(400, Math.round(curH)));
}
function fitWindow() {
  const chrome = $(".titlebar").offsetHeight + $("#nav").offsetHeight;
  const want = Math.min(MAX_H, Math.max(MIN_H, chrome + $("#content").offsetHeight));
  if (Math.abs(want - targetH) < 1) return;
  targetH = want;
  if (!raf) raf = requestAnimationFrame(stepHeight);
}
new ResizeObserver(fitWindow).observe($("#content")); // needs the shell above to exist

// ---------- navigation ----------
function showTab(next: Tab) {
  const leaving = tab;
  tab = next;
  closeDetail(false);
  $("#nav").style.setProperty("--i", String(TABS.findIndex(([k]) => k === next)));
  document.querySelectorAll<HTMLElement>("#nav [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === next));
  for (const [k] of TABS) pane(k).hidden = k !== next;
  if (leaving === "compare" && next !== "compare") leaveCompare();
  if (next === "compare" && leaving !== "compare") enterCompare();
  render();
}
$("#nav").addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest<HTMLElement>("[data-tab]");
  if (b) showTab(b.dataset.tab as Tab);
});

// ---------- song detail (opened from Add song or Collections) ----------
const detailHost = {
  onBack: () => closeDetail(true),
  onOpen: (id: string) => openDetail($("#detail"), id, detailHost),
  onRedrop: (url: string) => run(async () => { const { id } = await api.addJob(url); S.sessionJobs.add(id); closeDetail(false); showTab("add"); await refresh(); }),
};
function openSong(id: string) {
  pane(tab).hidden = true;
  detailHost.onOpen(id);
}
function closeDetail(restore: boolean) {
  const el = $("#detail");
  if (el.hidden) return;
  el.hidden = true;
  el.innerHTML = "";
  if (restore) { pane(tab).hidden = false; }
}

// ---------- views ----------
mountAddSong(pane("add"), { onOpen: openSong, refresh: () => refresh(true) });
mountCollections(pane("collections"), { onOpen: openSong, refresh: () => refresh(true) });
mountCompare($("#cmp"));

mountKeyPanel(() => render());
$("#keybtn").onclick = () => toggleKeyPanel();
$("#close").onclick = () => win?.close(); // the Rust side hides instead of quitting
$("#pause").onclick = () => run(async () => { await (S.status?.state === "paused_user" ? api.resume() : api.pause()); await refresh(true); });

function render() {
  const queued = (S.status?.jobs ?? []).filter((j) => j.status !== "done").length;
  $("#pause").classList.toggle("gone", !(queued > 0 || S.status?.state === "paused_user"));
  $("#pause").textContent = S.status?.state === "paused_user" ? "▶" : "❚❚";
  $("#offline").hidden = !S.offline;
  $("#keybtn").classList.toggle("on", !!S.llm?.configured);               // lit when a key is connected
  updateAddSong(pane("add"));
  updateCollections(pane("collections"));
}

// ---------- polling ----------
let doneCount = -1;
async function refresh(force = false) {
  try {
    S.status = await api.status();
    S.offline = false;
    const done = S.status.jobs.filter((j) => j.status === "done").length;
    if (done !== doneCount || force) {
      doneCount = done;
      [S.tracks, S.collections] = await Promise.all([api.tracks(), api.collections()]);
    }
    if (!S.llm) S.llm = await api.llm();
  } catch (e) {
    if (!S.offline) report(`service unreachable: ${String(e)}`);
    S.status = null;
    S.offline = true;
  }
  render();
}
showTab("add");
refresh(true);
setInterval(() => refresh(), 1200);
