// One song, opened: BPM, key, swing, then the ask box (and a way to fix a wrong drop).
import { api, type Similar, type TrackDoc } from "./api";
import { askBoxHtml, wireAsk } from "./ask";
import { $, esc, mmss, restart, run } from "./ui";

export interface DetailHost { onBack(): void; onRedrop(url: string): void; onOpen(id: string): void }

export function openDetail(el: HTMLElement, id: string, host: DetailHost) {
  return run(async () => {
    const [doc, similar] = await Promise.all([api.track(id), api.similar(id).catch(() => [] as Similar[])]);
    render(el, doc, similar, host);
  });
}

function render(el: HTMLElement, d: TrackDoc, similar: Similar[], host: DetailHost) {
  const swing = d.groove?.swing;
  const chips: [string, string][] = [];
  if (d.bpm) chips.push(["BPM", String(Math.round(d.bpm))]);
  if (d.key) chips.push(["Key", d.key]);
  if (swing !== undefined) chips.push(["Swing", `${swing >= 0 ? "+" : "−"}${Math.abs(Math.round(swing * 100))}%`]);
  const alts = d.alternative_drops?.length
    ? `<div class="rise" style="--i:4"><div class="section">Not the right drop?</div><div class="drops">${d.alternative_drops.map((t) => `<button class="btn ghost small" data-redrop="${t}">${mmss(t)}</button>`).join("")}</div></div>` : "";
  const sim = similar.length
    ? `<div class="rise" style="--i:5"><div class="section">Closest groove</div>${similar.map((s) => `<div class="card click" data-open="${esc(s.id)}"><div class="row"><div class="grow title">${esc(s.title || s.id)}</div><span class="status">${Math.round(s.score * 100)}%</span></div></div>`).join("")}</div>` : "";
  el.innerHTML = `<button class="back" data-back>← Back</button>
    <div class="rise" style="--i:0"><div class="h1">${esc(d.title)}</div><div class="sub">${esc(d.artist)}</div></div>
    <div class="chips">${chips.map(([k, v], i) => `<div class="chip big pop" style="--i:${i + 1}"><span>${esc(k)}</span>${esc(v)}</div>`).join("")}</div>
    <div class="rise" style="--i:3"><div class="section">Ask about this track</div>${askBoxHtml("e.g. What makes the groove feel the way it does?")}</div>${alts}${sim}`;
  wireAsk(el, (q) => api.ask(d.id, q));
  el.onclick = (e) => {
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-back],[data-redrop],[data-open]");
    if (!t) return;
    if (t.dataset.back !== undefined) host.onBack();
    else if (t.dataset.redrop) host.onRedrop(`${d.source}#t=${Number(t.dataset.redrop).toFixed(1)}`);
    else if (t.dataset.open) host.onOpen(t.dataset.open);
  };
  el.hidden = false;
  restart(el, "slide-in");
  $("#view").scrollTop = 0;
}
