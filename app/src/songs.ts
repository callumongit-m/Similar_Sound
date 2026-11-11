// How a song looks as a card, everywhere it appears.
import type { TrackSummary } from "./api";
import { esc, niceName } from "./ui";
import type { Card } from "./ui";

export const chip = (text: string, i = 0) => `<span class="chip pop" style="--i:${i}">${esc(text)}</span>`;

/** "B min / E min" when the key is a close call (a root and its fifth fit two keys), else just "B min". */
export const keyText = (key: string | null, alternatives: string[] = []) => (key ? [key, ...alternatives].join(" / ") : "");

export const trackChips = (t: TrackSummary) =>
  [t.bpm ? chip(`${Math.round(t.bpm)} bpm`, 0) : "", t.key ? chip(keyText(t.key, t.key_alternatives), 1) : ""].join("");

export const rowHtml = (title: string, artist: string, metaHtml: string) =>
  `<div class="row"><div class="grow"><div class="title">${esc(title)}</div><div class="sub">${esc(artist)}</div></div><div class="meta">${metaHtml}</div></div>`;

export function songCard(t: TrackSummary, opts: { cls?: string; extraMeta?: string; draggable?: boolean; keyPrefix?: string } = {}): Card {
  return {
    key: `${opts.keyPrefix ?? "t"}-${t.id}`,
    html: rowHtml(t.title || niceName(t.source), t.artist, trackChips(t) + (opts.extraMeta ?? "")),
    cls: `click ${opts.cls ?? ""}`,
    attrs: { track: t.id },
    draggable: opts.draggable,
  };
}
