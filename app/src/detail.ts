// One song, opened: BPM, key, swing, then the ask box (and a way to fix a wrong drop).
import { invoke } from "@tauri-apps/api/core";
import { api, type Similar, type TrackDoc } from "./api";
import { askSection, wireAsk } from "./ask";
import { keyText } from "./songs";
import { S } from "./state";
import { $, errorText, esc, mmss, restart, run, toast } from "./ui";

export interface DetailHost { onBack(): void; onRedrop(url: string): void; onOpen(id: string): void; onRemove(id: string): void }

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
  if (d.key) chips.push(["Key", keyText(d.key, (d as any).key_alternatives)]);
  if (swing !== undefined) chips.push(["Swing", `${swing >= 0 ? "+" : "−"}${Math.abs(Math.round(swing * 100))}%`]);
  const alts = d.alternative_drops?.length
    ? `<div class="rise" style="--i:4"><div class="section">Not the right drop?</div><div class="drops">${d.alternative_drops.map((t) => `<button class="btn ghost small" data-redrop="${t}">${mmss(t)}</button>`).join("")}</div></div>` : "";
  // Closed until asked for: a list of five songs on every open is a lot to take in when you came to look at one.
  const sim = similar.length
    ? `<div class="rise disclosure" style="--i:5"><button class="disc-head" data-disc aria-expanded="false"><span class="chev">›</span><span>Closest groove</span><span class="count">${similar.length}</span></button>
        <div class="disc-body"><div class="disc-inner">${similar.map((s) => `<div class="card click" data-open="${esc(s.id)}"><div class="row"><div class="grow title">${esc(s.title || s.id)}</div><span class="status">${Math.round(s.score * 100)}%</span></div></div>`).join("")}</div></div></div>` : "";
  // where the song lives, so "why isn't it in Unsorted?" has an answer on screen
  const homes = S.collections.filter((c) => c.track_ids.includes(d.id)).map((c) => c.name);
  el.innerHTML = `<div class="detail-top"><button class="back" data-back>← Back</button>
      <button class="removebtn" data-remove title="Forget this song so it can be analysed again from scratch">Remove</button></div>
    <div class="rise" style="--i:0"><div class="h1">${esc(d.title)}</div><div class="sub">${esc(d.artist)}</div>
      <div class="sub where">${homes.length ? `In ${esc(homes.join(", "))}` : "Unsorted"}</div>
      ${/^https?:\/\//.test(d.source) ? `<button class="linkrow play" data-play title="Open the song to hear it again">Play →</button>` : ""}</div>
    <div class="chips">${chips.map(([k, v], i) => `<div class="chip big pop" style="--i:${i + 1}"><span>${esc(k)}</span>${esc(v)}</div>`).join("")}</div>
    <div class="rise" style="--i:3">${askSection("Ask about this track", "e.g. What makes the groove feel the way it does?")}</div>${alts}${sim}`;
  wireAsk(el, (q, history) => api.ask(d.id, q, history));
  el.onclick = (e) => {
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-back],[data-redrop],[data-open],[data-remove],[data-play],[data-disc]");
    if (!t) return;
    if (t.dataset.play !== undefined) {            // the webview can't leave the app, so the app opens the link in your browser
      invoke("open_url", { url: d.source }).catch((err) => toast(errorText(err)));
    } else if (t.dataset.disc !== undefined) {
      const box = t.closest<HTMLElement>(".disclosure")!;
      const open = !box.classList.contains("open");
      box.classList.toggle("open", open);          // toggled in place, so the height eases open
      t.setAttribute("aria-expanded", String(open));
    } else if (t.dataset.remove !== undefined) {         // two clicks: removing forgets the analysis (and its place in every collection)
      if (t.dataset.armed) host.onRemove(d.id);
      else {
        t.dataset.armed = "1"; t.textContent = "Remove this song?"; t.classList.add("armed");
        setTimeout(() => { delete t.dataset.armed; t.textContent = "Remove"; t.classList.remove("armed"); }, 4000);
      }
    } else if (t.dataset.back !== undefined) host.onBack();
    else if (t.dataset.redrop) host.onRedrop(`${d.source}#t=${Number(t.dataset.redrop).toFixed(1)}`);
    else if (t.dataset.open) host.onOpen(t.dataset.open);
  };
  el.hidden = false;
  restart(el, "slide-in");
  $("#view").scrollTop = 0;
}
