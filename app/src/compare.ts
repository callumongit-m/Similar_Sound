// "Compare my project": an orb that waits for Ableton, listens while it plays, then lets you pick what to
// compare with and shows where your project differs.
import { api, type Comparison, type Feel, type Listen, type Why } from "./api";
import { askSection, wireAsk } from "./ask";
import { keyText } from "./songs";
import { S } from "./state";
import { $, errorText, esc, niceName, run, toast } from "./ui";

export const compareHtml = `<div id="cmp"></div>`;

let root: HTMLElement;
let phase = "";
let timer = 0;
let result: Comparison | null = null;

const orb = (cls: string, level = 0) => `<div class="orb ${cls}" style="--level:${level}"><i></i><i></i><i></i><i></i></div>`;
const stage = (cls: string, title: string, sub = "", level = 0) =>
  `<div class="stage">${orb(cls, level)}<div class="cmp-title">${esc(title)}</div><div class="cmp-sub">${esc(sub)}</div></div>`;

function pickerHtml(l: Listen) {
  const heard = l.heard ? `Heard ${Math.round(l.heard.bpm)} bpm${l.heard.key ? ` · ${keyText(l.heard.key, l.heard.key_alternatives)}` : ""}${l.heard.seconds ? ` · ${Math.round(l.heard.seconds)}s` : ""}` : "";
  const colls = S.collections.map((c) => `<div class="card click pick" data-kind="collection" data-id="${c.id}"><div class="row"><div class="grow title">${esc(c.name)}</div><span class="count">${c.track_ids.length}</span></div></div>`).join("");
  const songs = S.tracks.map((t) => `<div class="card click pick" data-kind="track" data-id="${esc(t.id)}"><div class="row"><div class="grow"><div class="title">${esc(t.title || niceName(t.source))}</div><div class="sub">${esc(t.artist)}</div></div></div></div>`).join("");
  return `<div class="stage small">${orb("s-ready")}<div class="cmp-title">Select the Song or Collection you'd like to compare with</div><div class="cmp-sub">${esc(heard)}</div></div>
    ${colls ? `<div class="section">Collections</div>${colls}` : ""}
    ${songs ? `<div class="section">Songs</div>${songs}` : `<div class="hint">Add a song first, then compare your project with it.</div>`}`;
}

function feelHtml(feel: Feel | undefined, why: Record<string, Why[]>) {
  if (!feel || !feel.aspects.length) return "";
  const rows = feel.aspects.map((a, i) => {
    const weak = a.kind === feel.weakest;
    const gap = a.gap === null ? "" : a.gap > 0.5 ? `${Math.round(a.gap)} further than typical` : "as close as typical";
    const names = a.closest && a.furthest ? `<div class="fr-sub">closest ${esc(a.closest.title)} ${a.closest.score} · furthest ${esc(a.furthest.title)} ${a.furthest.score}</div>` : "";
    const reasons = weak && (why[a.kind] ?? []).length
      ? `<div class="fr-why"><b>Furthest from the feel.</b> The measured differences behind it:<ul>${why[a.kind].map((w) => `<li>${esc(w.text)}</li>`).join("")}</ul></div>` : "";
    return `<div class="feelrow ${weak ? "weak" : ""}" style="--i:${i}">
      <div class="fr-head"><span class="fr-label">${esc(a.label)}</span><span class="fr-num">${a.score}<i>${a.typical === null ? "" : ` · typical ${a.typical}`}</i></span></div>
      <div class="fr-bar"><span class="fill" style="--v:${a.score}%"></span>${a.typical === null ? "" : `<span class="tick" style="--t:${a.typical}%"></span>`}</div>
      <div class="fr-sub">${esc(gap)}</div>${names}${reasons}</div>`;
  }).join("");
  return `<div class="section">How it feels</div><div class="sub wrap" style="margin:-4px 0 8px">How close your project sounds and grooves to the reference, measured the way a listener hears it. The line marks how close the reference songs are to each other.</div>
    ${feel.note ? `<div class="answer notice rise" style="--i:1">${esc(feel.note)}</div>` : ""}<div class="feel">${rows}</div>`;
}

function resultHtml(r: Comparison) {
  const rows = r.gaps.map((g, i) => {
    const [label, ...rest] = g.text.split(": ");
    return `<div class="gap ${g.outside ? "out" : ""}" style="--i:${i}"><span class="dir">${g.delta < 0 ? "↓" : "↑"}</span><div class="gt"><div class="gl">${esc(label)}</div><div class="gv">${esc(rest.join(": "))}</div></div></div>`;
  }).join("");
  return `<div class="cmp-head rise">Compared with <b>${esc(r.reference)}</b></div>
    <div class="cmp-links rise" style="--i:1">
      <button class="linkrow" data-rechoose>Compare to a different collection or song →</button>
      <button class="linkrow" data-again>Listen to my project again →</button>
    </div>
    ${r.small ? `<div class="answer notice rise" style="--i:1">${esc(r.note)}</div>` : ""}
    ${feelHtml(r.feel, r.why ?? {})}
    <div class="section">Where your project differs</div><div class="gaps">${rows}</div>
    ${askSection("Ask about this comparison", "e.g. What should I fix first?")}`;
}

function renderPhase(l: Listen) {
  const el = root;
  if (result) { el.innerHTML = resultHtml(result); wireAsk(el, (q, history) => api.askCompare(q, history)); }
  else if (l.state === "ready") el.innerHTML = pickerHtml(l);
  else if (l.state === "listening") el.innerHTML = stage("s-listening", "Listening", "", l.level);
  else if (l.state === "analysing") el.innerHTML = stage("s-analysing", "Analysing what I heard");
  else if (l.state === "error") el.innerHTML = `${stage("s-waiting", l.message)}<div style="text-align:center"><button class="btn small" data-retry>Try again</button></div>`;
  else el.innerHTML = stage("s-waiting", "Waiting for audio", l.message || "Press play in Ableton");
}

function apply(l: Listen) {
  S.listen = l;
  const key = result ? "result" : l.state === "idle" ? "waiting" : l.state;
  if (key !== phase) {
    phase = key;
    if (key === "ready") {                       // the picker lists your library: load it fresh, it may have changed
      Promise.all([api.tracks(), api.collections()]).then(([t, c]) => { S.tracks = t; S.collections = c; if (phase === "ready") renderPhase(l); })
        .catch(() => renderPhase(l));
    } else renderPhase(l);
    return;
  }
  const o = root.querySelector<HTMLElement>(".orb");
  o?.style.setProperty("--level", String(l.level));
  const sub = root.querySelector<HTMLElement>(".cmp-sub");
  if (sub && key === "waiting") sub.textContent = l.message || "Press play in Ableton";
}

async function tick() {
  try { apply(await api.listen()); } catch { /* offline: the main refresh shows it */ }
}

export function mountCompare(el: HTMLElement) {
  root = el;
  el.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const pick = t.closest<HTMLElement>("[data-kind]");
    if (pick) {
      run(async () => {
        result = await api.compare(pick.dataset.kind as "track" | "collection", pick.dataset.kind === "collection" ? Number(pick.dataset.id) : pick.dataset.id!);
        phase = "result";
        renderPhase(S.listen!);
      });
    } else if (t.closest("[data-rechoose]")) {
      result = null; phase = "";                 // the project is still heard: go back to choosing what to compare with
      tick();
    } else if (t.closest("[data-again],[data-retry]")) {
      result = null; phase = "";
      run(async () => { apply(await api.listenStart()); });
    }
  });
}

export async function enterCompare() {
  clearInterval(timer);
  try {
    let l = await api.listen();
    if (l.state === "idle" || l.state === "error") { result = null; l = await api.listenStart(); }
    phase = ""; apply(l);
  } catch (e) { toast(errorText(e)); }
  timer = window.setInterval(tick, 250);
}

export async function leaveCompare() {
  clearInterval(timer);
  const l = S.listen;
  if (l && (l.state === "waiting" || l.state === "listening")) { result = null; phase = ""; await api.listenCancel().catch(() => {}); }
}
