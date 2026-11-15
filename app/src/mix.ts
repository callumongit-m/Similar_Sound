// "Mix": the live spectrum of your Ableton mix, drawn as a coloured wave. The service streams ~30 frames a second of
// band levels (dB, 20 Hz to 20 kHz, log-spaced); this file only smooths and paints them. Audio never reaches the window.
import { BASE } from "./api";
import { $ } from "./ui";

export const mixHtml = `<div class="mix">
  <canvas id="mixcv" width="372" height="230"></canvas>
  <div class="mix-status" id="mixmsg">Connecting…</div>
</div>`;

const DB_TOP = -6, DB_BOTTOM = -84;          // the range the wave is drawn in
const TILT_DB_PER_OCT = 4.5;                  // music falls ~4.5 dB per octave; tilting makes a balanced mix look flat
const FREQS = [50, 100, 200, 500, 1000, 2000, 5000, 10000];
const label = (f: number) => (f >= 1000 ? `${f / 1000}k` : String(f));
/** The wave's colours, low level to high level. One place to tune them. */
export const PALETTE = ["#2a1a6e", "#5a2fc2", "#b83ad6", "#ff5c8a", "#ffb15c"];

let abort: AbortController | null = null;
let raf = 0;
let target: number[] = [];                    // latest frame, tilted
let shown: number[] = [];                     // what is drawn: rises at once, falls smoothly
let peaks: number[] = [];                     // slow-falling peak line
let lastFrame = 0;

function tilt(i: number, n: number, db: number) {
  const f = 20 * Math.pow(1000, i / (n - 1));                // band centre, 20 Hz to 20 kHz
  return db + TILT_DB_PER_OCT * Math.log2(f / 1000);
}

function paint(cv: HTMLCanvasElement) {
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 372, h = cv.clientHeight || 230;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
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

function animate(cv: HTMLCanvasElement) {
  if (target.length) {
    if (shown.length !== target.length) { shown = target.slice(); peaks = target.slice(); }
    const idle = performance.now() - lastFrame > 600;          // no frames: let the wave settle to the floor
    for (let i = 0; i < shown.length; i++) {
      const t = idle ? DB_BOTTOM : target[i];
      shown[i] = t > shown[i] ? t : shown[i] + (t - shown[i]) * 0.22;
      peaks[i] = shown[i] > peaks[i] ? shown[i] : peaks[i] - 0.35;
    }
  }
  paint(cv);
  raf = requestAnimationFrame(() => animate(cv));
}

function handle(frame: any) {
  const msg = $("#mixmsg");
  if (frame.state === "live") {
    const bands: number[] = frame.bands;
    target = bands.map((db, i) => tilt(i, bands.length, db));
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

export function enterMix() {
  leaveMix();
  abort = new AbortController();
  target = []; shown = []; peaks = [];
  $("#mixmsg").textContent = "Connecting…";
  raf = requestAnimationFrame(() => animate($<HTMLCanvasElement>("#mixcv")));
  stream(abort.signal);
}

/** Closing the stream is what stops the audio helper in the service: nothing listens while the tab is not open. */
export function leaveMix() {
  abort?.abort(); abort = null;
  cancelAnimationFrame(raf); raf = 0;
}
