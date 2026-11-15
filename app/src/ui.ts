// Shared helpers: DOM shortcuts, the keyed card list (only new cards animate in), toast, naming.
import { ApiError } from "./api";

export const $ = <T extends HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector(sel) as T;
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
export const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export const restart = (el: HTMLElement, cls: string) => { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); };
export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

let toastTimer = 0;
export function toast(e: unknown) {
  const el = $("#toast");
  el.textContent = e instanceof Error ? e.message : String(e);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove("show"), 5000);
}
export const run = async (fn: () => Promise<unknown>) => { try { await fn(); } catch (e) { toast(e); } };
export const errorText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

/** Name guessed from the URL, used only until the real title arrives (about a second). */
export function niceName(source: string): string {
  const base = source.split("#")[0].split("?")[0];
  const seg = base.split("/").filter(Boolean).pop() ?? base;
  if (/^on\.soundcloud\.com/.test(base)) return "SoundCloud link";
  return decodeURIComponent(seg).replace(/-\d+$/, "").replace(/[-_]+/g, " "); // "-5" is SoundCloud's slug suffix
}

export interface Card {
  key: string;
  html: string;
  cls?: string;                                   // extra classes (beyond "card")
  attrs?: Record<string, string | undefined>;     // data-* attributes
  draggable?: boolean;
}
type CardEl = HTMLElement & { _html?: string };

/** Keyed reconcile: existing cards are updated in place, new ones animate in, removed ones animate out.
 *  Input values and focus inside a card survive an update. */
const TRANSIENT = ["dragging", "drop", "bump"];   // classes the views add and remove themselves
export const dragging = { active: false };      // a drag is in progress: lists hold still until it ends

export function syncCards(container: HTMLElement, cards: Card[]) {
  if (dragging.active) return;
  const existing = new Map<string, CardEl>();
  container.querySelectorAll<CardEl>(":scope > .card").forEach((el) => { if (!el.classList.contains("leaving")) existing.set(el.dataset.key!, el); });
  const wanted = new Set(cards.map((c) => c.key));
  for (const [key, el] of existing) {
    if (wanted.has(key)) continue;
    el.classList.add("leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 500); // safety net if animations are throttled
  }
  let cursor: Element | null = container.firstElementChild;
  for (const c of cards) {
    let el = existing.get(c.key);
    const fresh = !el;
    if (!el) {
      el = document.createElement("div") as CardEl;
      el.dataset.key = c.key;
      el.addEventListener("animationend", (e) => { if (e.target === el) el!.classList.remove("enter"); });
    }
    // Only write what changed. This runs every second or so, and rewriting a draggable card's class or `draggable`
    // while the mouse is down cancels the browser's pending drag (the cursor flickers and the drag never starts).
    const want = `card${fresh ? " enter" : el.classList.contains("enter") ? " enter" : ""} ${c.cls ?? ""}`.trim();
    const stable = (names: string[]) => names.filter((k) => !TRANSIENT.includes(k)).sort().join(" ");
    if (stable([...el.classList]) !== stable(want.split(/\s+/))) {
      el.className = [want, ...TRANSIENT.filter((k) => el.classList.contains(k))].join(" ");
    }
    if (el.draggable !== !!c.draggable) el.draggable = !!c.draggable;
    for (const [k, v] of Object.entries(c.attrs ?? {})) {
      if (v === undefined) { if (el.hasAttribute(`data-${k}`)) el.removeAttribute(`data-${k}`); }
      else if (el.getAttribute(`data-${k}`) !== v) el.setAttribute(`data-${k}`, v);
    }
    if (el._html !== c.html) {
      const kept = new Map<string, string>();
      el.querySelectorAll<HTMLInputElement>("[data-keep]").forEach((i) => kept.set(i.dataset.keep!, i.value));
      const focus = (document.activeElement as HTMLElement | null)?.dataset?.keep;
      el.innerHTML = c.html;
      el._html = c.html;
      el.querySelectorAll<HTMLInputElement>("[data-keep]").forEach((i) => { if (kept.has(i.dataset.keep!)) i.value = kept.get(i.dataset.keep!)!; });
      if (focus) el.querySelector<HTMLElement>(`[data-keep="${focus}"]`)?.focus();
    }
    while (cursor && (cursor as HTMLElement).classList.contains("leaving")) cursor = cursor.nextElementSibling;
    if (cursor === el) cursor = el.nextElementSibling;
    else container.insertBefore(el, cursor);
  }
}
