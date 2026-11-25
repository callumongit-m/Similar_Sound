// "Mix": your Ableton mix live, as a Spectrum (levels by frequency) or a Waveform (level over time).
// The service streams ~30 frames a second: band levels (dB, 20 Hz to 20 kHz, log-spaced), peaks per time slice and the
// low/mid/high energy split. This file only smooths and paints them. Audio never reaches the window.
import { BASE } from "./api";
import { $ } from "./ui";

const opts = (list: [string, string][]) => list.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
export const mixHtml = `<div class="mix">
  <div class="mix-bar">
    <select id="mixview" aria-label="Meter">${opts([["spectrum", "Spectrum"], ["waveform", "Waveform"]])}</select>
    <span class="mix-wave" id="mixwaveopts" hidden>
      <select id="mixchan" aria-label="Channels">${opts([["mid", "Mid"], ["left", "Left"], ["right", "Right"], ["side", "Side"], ["lr", "Left / Right"], ["ms", "Mid / Side"]])}</select>
      <select id="mixcolor" aria-label="Colour">${opts([["multi", "Multi-band"], ["map", "Colour map"], ["static", "Static"]])}</select>
      <select id="mixspeed" aria-label="Speed">${opts([["12", "Slow"], ["6", "Normal"], ["3", "Fast"]])}</select>
      <select id="mixloop" aria-label="Display">${opts([["scroll", "Scrolling"], ["loop", "Looping"]])}</select>
    </span>
  </div>
  <canvas id="mixcv" width="372" height="230"></canvas>
  <div class="mix-status" id="mixmsg">Connecting…</div>
</div>`;

const DB_TOP = -6, DB_BOTTOM = -84;          // the range the wave is drawn in
const TILT_DB_PER_OCT = 4.5;                  // music falls ~4.5 dB per octave; tilting makes a balanced mix look flat
const FREQS = [50, 100, 200, 500, 1000, 2000, 5000, 10000];
const label = (f: number) => (f >= 1000 ? `${f / 1000}k` : String(f));
/** The wave's colours, low level to high level. One place to tune them. */
export const PALETTE = ["#2a1a6e", "#5a2fc2", "#b83ad6", "#ff5c8a", "#ffb15c"];

// ---------- settings (remembered between runs) ----------
const SETTINGS_KEY = "similar-sound.mix";
const settings = { view: "spectrum", chan: "mid", color: "multi", speed: "6", loop: "scroll" };
try { Object.assign(settings, JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}")); } catch { /* private window: defaults */ }
const save = () => { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* not worth a failure */ } };

// ---------- state ----------
let abort: AbortController | null = null;
let raf = 0;
let target: number[] = [];                    // spectrum: latest frame, tilted
let shown: number[] = [];                     // what is drawn: rises at once, falls smoothly
let peaks: number[] = [];                     // slow-falling peak line
let lastFrame = 0;

const COLS_PER_S = 120;                       // 60 frames a second x 2 slices
const LATENCY_COLS = 14;                      // the picture runs ~0.12 s behind the newest audio, so frames arriving in bursts never starve it
const FIELDS = ["L", "R", "M", "S", "lo", "mid", "hi"] as const;
type Field = (typeof FIELDS)[number];
const seconds = () => Number(settings.speed);
const windowCols = () => Math.round(seconds() * COLS_PER_S);
const capacity = () => windowCols() + (settings.loop === "loop" ? 0 : 256);   // a looping picture is exactly one window long
let ring: Record<Field, Float32Array> = makeRing();
let recv = 0;                                 // columns received so far: the absolute index of the next one
let head = 0;                                 // the playhead, in columns: advanced by the clock, not by arrivals
let lastTick = 0;
function makeRing() { return Object.fromEntries(FIELDS.map((f) => [f, new Float32Array(capacity())])) as Record<Field, Float32Array>; }
function resetRing() { ring = makeRing(); recv = 0; head = 0; lastTick = 0; }
function pushColumn(peak: number[], share: number[]) {
  const v = [...peak, ...share], at = recv % capacity();
  FIELDS.forEach((f, i) => { ring[f][at] = v[i]; });
  recv++;
}

// ---------- spectrum ----------
function tilt(i: number, n: number, db: number) {
  const f = 20 * Math.pow(1000, i / (n - 1));                // band centre, 20 Hz to 20 kHz
  return db + TILT_DB_PER_OCT * Math.log2(f / 1000);
}

function setup(cv: HTMLCanvasElement) {
  const ctx = cv.getContext("2d");
  if (!ctx) return null;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 372, h = cv.clientHeight || 230;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function paintSpectrum(cv: HTMLCanvasElement) {
  const g = setup(cv);
  if (!g) return;
  const { ctx, w, h } = g;
  const padB = 16, plotH = h - padB;
  const y = (db: number) => plotH * (1 - Math.min(1, Math.max(0, (db - DB_BOTTOM) / (DB_TOP - DB_BOTTOM))));

  ctx.font = "9px -apple-system, sans-serif";
  ctx.textAlign = "center";
  for (const f of FREQS) {                                    // frequency grid
    const x = (Math.log(f / 20) / Math.log(1000)) * w;
    ctx.fillStyle = "rgba(255,255,255,0.07)"; ctx.fillRect(Math.round(x), 0, 1, plotH);
    ctx.fillStyle = "rgba(255,255,255,0.4)"; ctx.fillText(label(f), x, h - 4);
  }
  for (let db = -24; db > DB_BOTTOM; db -= 24) { ctx.fillStyle = "rgba(255,255,255,0.06)"; ctx.fillRect(0, Math.round(y(db)), w, 1); }
  const n = shown.length;
  if (n < 2) return;

  const xs = shown.map((_, i) => (i / (n - 1)) * w);
  const curve = (vals: number[]) => {                          // smooth line through the band levels
    ctx.beginPath();
    ctx.moveTo(xs[0], y(vals[0]));
    for (let i = 1; i < n - 1; i++) ctx.quadraticCurveTo(xs[i], y(vals[i]), (xs[i] + xs[i + 1]) / 2, (y(vals[i]) + y(vals[i + 1])) / 2);
    ctx.lineTo(xs[n - 1], y(vals[n - 1]));
  };
  const fill = ctx.createLinearGradient(0, plotH, 0, 0);
  PALETTE.forEach((c, i) => fill.addColorStop(i / (PALETTE.length - 1), c));
  curve(shown);
  ctx.lineTo(w, plotH); ctx.lineTo(0, plotH); ctx.closePath();
  ctx.globalAlpha = 0.78; ctx.fillStyle = fill; ctx.fill(); ctx.globalAlpha = 1;
  curve(shown);
  ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.strokeStyle = fill; ctx.shadowColor = PALETTE[3]; ctx.shadowBlur = 8; ctx.stroke();
  ctx.shadowBlur = 0;
  curve(peaks);
  ctx.lineWidth = 1; ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.stroke();
}

let lastSpec = 0;
/** Smoothing by elapsed time, not per drawn frame, so it looks the same at 60 and 120 Hz and the 60-a-second data never steps. */
function stepSpectrum(now: number) {
  const dt = lastSpec ? Math.min(0.1, (now - lastSpec) / 1000) : 0;
  lastSpec = now;
  if (!target.length) return;
  if (shown.length !== target.length) { shown = target.slice(); peaks = target.slice(); }
  const idle = now - lastFrame > 600;                          // no frames: let the wave settle to the floor
  const rise = 1 - Math.exp(-dt / 0.02), fall = 1 - Math.exp(-dt / 0.09);
  for (let i = 0; i < shown.length; i++) {
    const t = idle ? DB_BOTTOM : target[i];
    shown[i] += (t - shown[i]) * (t > shown[i] ? rise : fall);
    peaks[i] = shown[i] > peaks[i] ? shown[i] : peaks[i] - 20 * dt;   // the peak line falls 20 dB a second
  }
}

// ---------- waveform ----------
/** Bass warm, mids violet, highs cool: each pixel is blended by how much of the energy sits in each region. */
export const BAND_COLORS: [number, number, number][] = [[255, 92, 138], [160, 80, 230], [92, 200, 255]];
const hex = (c: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)) as [number, number, number];
const STOPS = PALETTE.map(hex);
function mapColor(a: number): string {                       // 0..1 level -> palette
  const x = Math.min(1, Math.max(0, a)) * (STOPS.length - 1), i = Math.min(STOPS.length - 2, Math.floor(x)), f = x - i;
  return `rgb(${STOPS[i].map((v, k) => Math.round(v + (STOPS[i + 1][k] - v) * f)).join(",")})`;
}
const CHANNELS: Record<string, Field[]> = { mid: ["M"], left: ["L"], right: ["R"], side: ["S"], lr: ["L", "R"], ms: ["M", "S"] };
const LANE_NAME: Record<Field, string> = { L: "L", R: "R", M: "Mid", S: "Side", lo: "", mid: "", hi: "" };
const shape = (a: number) => Math.pow(Math.min(1, a), 0.7);  // a little lift so quiet passages stay visible

/** Rigid scrolling: columns are grouped into fixed bins (by absolute index, so a column never changes bin) and each bin is
 *  drawn at a fractional x from the steady playhead. Nothing is re-sampled from frame to frame, so nothing shimmers. */
function paintWaveform(cv: HTMLCanvasElement) {
  const g = setup(cv);
  if (!g) return;
  const { ctx, w, h } = g;
  const padB = 16, plotH = h - padB, N = windowCols(), cap = capacity();
  const lanes = CHANNELS[settings.chan] ?? CHANNELS.mid;
  const laneH = plotH / lanes.length;
  const loop = settings.loop === "loop";
  const pxPerCol = w / N, B = Math.max(1, Math.round(1 / pxPerCol));
  const first = loop ? 0 : Math.max(0, Math.floor((head - N) / B) + 1);     // oldest bin still in the window
  const last = loop ? Math.floor(N / B) : Math.floor(head / B);              // bins fully played (the newest is still filling)

  ctx.font = "9px -apple-system, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.textAlign = "left"; ctx.fillText(loop ? "0s" : `-${seconds()}s`, 2, h - 4);
  ctx.textAlign = "right"; ctx.fillText(loop ? `${seconds()}s` : "now", w - 2, h - 4);

  lanes.forEach((field, li) => {
    const cy = li * laneH + laneH / 2, half = laneH / 2 - 3;
    ctx.fillStyle = "rgba(255,255,255,0.08)"; ctx.fillRect(0, Math.round(cy), w, 1);
    if (lanes.length > 1) { ctx.fillStyle = "rgba(255,255,255,0.4)"; ctx.textAlign = "left"; ctx.fillText(LANE_NAME[field], 4, li * laneH + 11); }
    const st = ctx.createLinearGradient(0, cy - half, 0, cy + half);
    st.addColorStop(0, PALETTE[3]); st.addColorStop(0.5, PALETTE[1]); st.addColorStop(1, PALETTE[3]);
    for (let k = first; k < last; k++) {
      let peak = 0, lo = 0, mid = 0, hi = 0;
      for (let c = k * B; c < (k + 1) * B; c++) {
        if (!loop && (c >= recv || c < recv - cap)) continue;                // not received yet, or already overwritten
        const at = c % cap;
        peak = Math.max(peak, ring[field][at]);
        lo += ring.lo[at]; mid += ring.mid[at]; hi += ring.hi[at];
      }
      const a = shape(peak);
      if (a < 0.004) continue;
      const x = loop ? k * B * pxPerCol : w - (head - k * B) * pxPerCol;
      if (x + B * pxPerCol < 0) continue;
      if (settings.color === "static") ctx.fillStyle = st;
      else if (settings.color === "map") ctx.fillStyle = mapColor(a);
      else {
        const t = (lo + mid + hi) || 1;
        ctx.fillStyle = `rgb(${[0, 1, 2].map((q) => Math.round((BAND_COLORS[0][q] * lo + BAND_COLORS[1][q] * mid + BAND_COLORS[2][q] * hi) / t)).join(",")})`;
      }
      ctx.fillRect(x, cy - a * half, B * pxPerCol + 0.6, Math.max(1, a * half * 2));   // +0.6 hides hairline seams between bins
    }
  });
  if (loop) { ctx.fillStyle = "rgba(255,255,255,0.7)"; ctx.fillRect(Math.round(((head % N) / N) * w), 0, 1, plotH); }
}

/** Advance the playhead by real elapsed time, nudged toward "newest audio minus the latency" so it neither drifts nor jumps. */
function stepWaveform(now: number) {
  const dt = lastTick ? Math.min(0.1, (now - lastTick) / 1000) : 0;
  lastTick = now;
  const idle = now - lastFrame > 600;
  if (idle) { for (let i = 0; i < Math.max(1, Math.round(dt * COLS_PER_S)); i++) pushColumn([0, 0, 0, 0], [0, 0, 0]); }
  const aim = recv - LATENCY_COLS;
  head += dt * COLS_PER_S + (aim - head) * Math.min(1, dt * 4);
  if (head > recv) head = recv;
  if (settings.loop === "loop" && recv === 0) head = 0;
}

function animate(cv: HTMLCanvasElement) {
  const now = performance.now();
  if (settings.view === "waveform") { stepWaveform(now); paintWaveform(cv); }
  else { stepSpectrum(now); paintSpectrum(cv); }
  raf = requestAnimationFrame(() => animate(cv));
}

// ---------- the stream ----------
function handle(frame: any) {
  const msg = $("#mixmsg");
  if (frame.state === "live") {
    const bands: number[] = frame.bands;
    target = bands.map((db, i) => tilt(i, bands.length, db));
    for (const c of frame.cols ?? []) pushColumn(c, frame.tri ?? [0, 0, 0]);
    lastFrame = performance.now();
    msg.textContent = "";
  } else {
    target = target.map(() => DB_BOTTOM);
    msg.textContent = frame.message || "Press play in Ableton";
  }
}

async function stream(signal: AbortSignal) {
  while (!signal.aborted) {
    try {
      const res = await fetch(`${BASE}/mix/stream`, { signal });
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut: number;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, cut).trim();
          buf = buf.slice(cut + 2);
          if (line.startsWith("data: ")) handle(JSON.parse(line.slice(6)));
        }
      }
    } catch { if (signal.aborted) return; $("#mixmsg").textContent = "Waiting for the service…"; }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// ---------- the controls ----------
function syncControls() {
  const set = (id: string, v: string) => { $<HTMLSelectElement>(`#${id}`).value = v; };
  set("mixview", settings.view); set("mixchan", settings.chan); set("mixcolor", settings.color); set("mixspeed", settings.speed); set("mixloop", settings.loop);
  $("#mixwaveopts").hidden = settings.view !== "waveform";
}

export function mountMix(root: HTMLElement) {
  root.addEventListener("change", (e) => {
    const el = e.target as HTMLSelectElement;
    const key = ({ mixview: "view", mixchan: "chan", mixcolor: "color", mixspeed: "speed", mixloop: "loop" } as Record<string, keyof typeof settings>)[el.id];
    if (!key) return;
    settings[key] = el.value;
    if (key === "speed" || key === "loop") resetRing();         // a different time window: start the picture afresh
    save(); syncControls();
  });
}

export function enterMix() {
  leaveMix();
  abort = new AbortController();
  target = []; shown = []; peaks = []; lastSpec = 0; resetRing();
  syncControls();
  $("#mixmsg").textContent = "Connecting…";
  raf = requestAnimationFrame(() => animate($<HTMLCanvasElement>("#mixcv")));
  stream(abort.signal);
}

/** Closing the stream is what stops the audio helper in the service: nothing listens while the tab is not open. */
export function leaveMix() {
  abort?.abort(); abort = null;
  cancelAnimationFrame(raf); raf = 0;
}
