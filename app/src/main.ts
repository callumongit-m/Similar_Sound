import { invoke } from "@tauri-apps/api/core";
import { LogicalSize } from "@tauri-apps/api/dpi";
import { currentMonitor, getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import { addSongHtml, mountAddSong, updateAddSong } from "./addSong";
import { api } from "./api";
import { openCollectionChat } from "./chat";
import { collectionsHtml, mountCollections, updateCollections } from "./collections";
import { compareHtml, enterCompare, leaveCompare, mountCompare } from "./compare";
import { openDetail } from "./detail";
import { enterMix, leaveMix, mixHtml } from "./mix";
import { keyPanelHtml, mountKeyPanel, toggleKeyPanel } from "./keypanel";
import { BOOT_GRACE_MS, LIBRARY_SYNC_MS, S } from "./state";
import { $, run, toast } from "./ui";

// ---------- diagnostics: errors and startup go to ~/.similar_sound/app.log (via the Rust side) ----------
const report = (msg: string) => { invoke("log", { msg }).catch(() => {}); };
window.addEventListener("error", (e) => report(`error: ${e.message} @ ${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => report(`unhandled rejection: ${String((e as PromiseRejectionEvent).reason)}`));
report(`page started (${location.href})`);

// ---------- ghost: the shape of the window before the service has answered ----------
function ghostCards(n: number): string {
  return Array.from({ length: n }, (_, i) => `<div class="card ghost" style="--i:${i}"><div class="row"><div class="grow"><div class="gline w60" style="--i:${i}"></div><div class="gline w35" style="--i:${i + 1}"></div></div>
    <div class="gchips"><span class="gchip" style="--i:${i}"></span><span class="gchip" style="--i:${i + 1}"></span></div></div></div>`).join("");
}

// ---------- shell ----------
type Tab = "add" | "collections" | "compare" | "mix";
const TABS: [Tab, string][] = [["add", "Add song"], ["collections", "Collections"], ["compare", "Compare my project"], ["mix", "Mix"]];
let tab: Tab = "add";
const KEY_ICON = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4.2"/><path d="M11 12l9-9M16.5 6.5l2.5 2.5M14 9l2 2"/></svg>`;

$("#app").innerHTML = `
  <div class="titlebar" data-tauri-drag-region>
    <button class="iconbtn corner keybtn" id="keybtn" title="API key">${KEY_ICON}</button>
    <span class="grabber" data-tauri-drag-region></span>
    <span class="spacer" data-tauri-drag-region></span>
    <button class="iconbtn corner" id="close" title="Hide (the ♪ in the menu bar brings it back)">✕</button>
  </div>
  <div class="nav" id="nav" style="--i:0"><span class="slider"></span>${TABS.map(([k, label]) => `<button data-tab="${k}">${label}</button>`).join("")}</div>
  <div id="view"><div id="content">
    ${keyPanelHtml}
    <div class="hint" id="offline" hidden><b>Service not running</b>Start it with <code>service/run.sh</code>. This window reconnects by itself.</div>
    <section id="pane-add" class="pane">${addSongHtml}<div class="ghostlist">${ghostCards(1)}</div></section>
    <section id="pane-collections" class="pane" hidden>${collectionsHtml}<div class="ghostlist">${ghostCards(3)}</div></section>
    <section id="pane-compare" class="pane" hidden>${compareHtml}</section>
    <section id="pane-mix" class="pane" hidden>${mixHtml}</section>
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
/** The window grows downward from its top edge, so a tall collection near the bottom of the screen would push its
 *  lower half off-screen, out of reach. Slide the window up just enough to keep its bottom edge visible. */
async function keepOnScreen(height: number) {
  if (!win) return;
  try {
    const m = await currentMonitor();
    if (!m) return;
    const area = m.workArea ?? { position: m.position, size: m.size };
    const px = (n: number) => Math.round(n * m.scaleFactor);
    const bottom = area.position.y + area.size.height - px(8);
    const pos = await win.outerPosition();
    if (pos.y + px(height) > bottom) await win.setPosition(new PhysicalPosition(pos.x, Math.max(area.position.y, bottom - px(height))));
  } catch { /* moving the window is a nicety: never let it break resizing */ }
}

function fitWindow() {
  const chrome = $(".titlebar").offsetHeight + $("#nav").offsetHeight;
  const want = Math.min(MAX_H, Math.max(MIN_H, chrome + $("#content").offsetHeight));
  if (Math.abs(want - targetH) < 1) return;
  targetH = want;
  keepOnScreen(want);
  if (!raf) raf = requestAnimationFrame(stepHeight);
}
new ResizeObserver(fitWindow).observe($("#content")); // needs the shell above to exist

// ---------- navigation ----------
/** The highlight follows the active button's own box: the tabs are not all the same width. */
function moveSlider() {
  const b = document.querySelector<HTMLElement>("#nav [data-tab].active");
  const s = $("#nav .slider");
  if (b) { s.style.width = `${b.offsetWidth}px`; s.style.transform = `translateX(${b.offsetLeft - 3}px)`; }
}
function showTab(next: Tab) {
  const leaving = tab;
  tab = next;
  closeDetail(false);
  document.querySelectorAll<HTMLElement>("#nav [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === next));
  $("#nav").style.setProperty("--i", String(TABS.findIndex(([k]) => k === next)));   // also the state tests read
  moveSlider();
  for (const [k] of TABS) pane(k).hidden = k !== next;
  if (leaving === "compare" && next !== "compare") leaveCompare();
  if (leaving === "mix" && next !== "mix") leaveMix();
  if (next === "mix" && leaving !== "mix" && !S.booting) enterMix();
  if (next === "compare" && leaving !== "compare" && !S.booting) enterCompare();   // while the service starts, Compare shows its ghost
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
  onRemove: (id: string) => run(async () => {
    await api.deleteTrack(id);
    closeDetail(true);
    toast("Removed. Paste its link again to analyse it from scratch.");
    await refresh(true);
  }),
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
mountCollections(pane("collections"), {
  onOpen: openSong,
  onChat: (cid) => {
    const c = S.collections.find((x) => x.id === cid);
    if (!c) return;
    pane(tab).hidden = true;
    openCollectionChat($("#detail"), { id: c.id, name: c.name, count: c.track_ids.length }, { onBack: () => closeDetail(true) });
  },
  refresh: () => refresh(true),
});
mountCompare($("#cmp"));

mountKeyPanel(() => render());
$("#keybtn").onclick = () => toggleKeyPanel();
$("#close").onclick = () => win?.close(); // the Rust side hides instead of quitting

function render() {
  $("#offline").hidden = !S.offline;
  const was = document.body.classList.contains("booting");
  document.body.classList.toggle("booting", S.booting);
  const url = $<HTMLInputElement>("#url");
  url.disabled = S.booting;
  url.placeholder = S.booting ? "Getting ready…" : "Paste a SoundCloud link";
  if (was && !S.booting && tab === "compare") enterCompare();                         // the service is up: Compare can start listening
  $("#keybtn").classList.toggle("on", !!S.llm?.configured);               // lit when a key is connected
  updateAddSong(pane("add"));
  updateCollections(pane("collections"));
}

// ---------- polling ----------
let doneCount = -1;
let lastLibrarySync = 0;
const bootStart = Date.now();
async function refresh(force = false) {
  try {
    S.status = await api.status();
    S.offline = false;
    S.booting = false;
    const done = S.status.jobs.filter((j) => j.status === "done").length;
    if (done !== doneCount || force || Date.now() - lastLibrarySync > LIBRARY_SYNC_MS) {
      doneCount = done;
      lastLibrarySync = Date.now();
      [S.tracks, S.collections] = await Promise.all([api.tracks(), api.collections()]);
    }
    if (!S.llm) S.llm = await api.llm();
  } catch (e) {
    if (!S.offline && !S.booting) report(`service unreachable: ${String(e)}`);
    S.status = null;
    if (S.booting && Date.now() - bootStart > BOOT_GRACE_MS) S.booting = false;     // it should have started by now
    S.offline = !S.booting;                                                          // until then it is just starting up
  }
  render();
}
showTab("add");
refresh(true);
setInterval(() => refresh(), 1200);
