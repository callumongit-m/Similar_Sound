// Chat with a whole collection: the model is given every song in it together (what they share, what varies, the
// common drum steps), so "what do these songs have in common?" is a question about the collection, not one song.
import { api } from "./api";
import { askSection, wireAsk } from "./ask";
import { $, esc, restart } from "./ui";

export interface ChatHost { onBack(): void }

export function openCollectionChat(el: HTMLElement, c: { id: number; name: string; count: number }, host: ChatHost) {
  const songs = `${c.count} song${c.count === 1 ? "" : "s"}`;
  el.innerHTML = `<div class="detail-top"><button class="back" data-back>← Back</button></div>
    <div class="rise" style="--i:0"><div class="h1">${esc(c.name)}</div><div class="sub">${songs}</div></div>
    <div class="rise" style="--i:1">${askSection("Chat about this collection", "e.g. What do these songs have in common?",
      `Answers draw on all ${songs} in this collection together.`)}</div>`;
  wireAsk(el, (q, history) => api.askCollection(c.id, q, history));
  el.onclick = (e) => { if ((e.target as HTMLElement).closest("[data-back]")) host.onBack(); };
  el.hidden = false;
  restart(el, "slide-in");
  $("#view").scrollTop = 0;
}
