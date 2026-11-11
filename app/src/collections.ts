// "Collections": the whole library as folders. Drag a song onto a folder to add it (a song can be in
// several); songs in no folder sit under "Unsorted".
import { api, type Collection } from "./api";
import { S, trackById } from "./state";
import { songCard } from "./songs";
import { $, errorText, esc, niceName, run, syncCards, toast } from "./ui";
import type { Card } from "./ui";

export interface CollHost { onOpen(id: string): void; onChat(collectionId: number): void; refresh(): Promise<void> }

/** Shown on hover of the (i) beside "New collection". It describes how the drag and remove gestures below work. */
export const INFO_TIP = "You can have the same track in multiple collections. To do this you must first create the new collection and then drag the track from the one collection into the other. To remove tracks from collections you must click the 'x' icon which appears when your mouse is hovering over that track.";

export const collectionsHtml = `
  <div class="coll-bar">
    <span class="info" tabindex="0" aria-describedby="infotip"><b>i</b>
      <span class="infotip" id="infotip" role="tooltip">${INFO_TIP}</span></span>
    <button class="btn ghost small" data-new>＋ New collection</button>
  </div>
  <div class="newrow" id="newrow" hidden><input class="field" id="newname" placeholder="Name, e.g. UK minimal" maxlength="40" spellcheck="false" /><button class="btn small" data-create>Create</button></div>
  <div id="folders"></div>
  <div class="section" id="unsorted-title" hidden>Unsorted</div>
  <div id="unsorted"></div>
  <div class="hint" id="coll-empty" hidden><b>Nothing here yet</b>Songs you analyse land here. Drag them into collections like "UK minimal" or "rolling bass".</div>`;

const open = new Set<number>();

const miniRow = (cid: number, trackId: string) => {
  const t = trackById(trackId);
  if (!t) return "";
  return `<div class="mini" draggable="true" data-track="${esc(t.id)}"><div class="grow"><span class="mt">${esc(t.title || niceName(t.source))}</span><span class="ma">${esc(t.artist)}</span></div>
    <button class="x" data-remove="${esc(t.id)}" data-cid="${cid}" title="Take out of this collection">✕</button></div>`;
};

function folderCard(c: Collection): Card {
  const rows = c.track_ids.map((id) => miniRow(c.id, id)).join("");
  return {
    key: `c-${c.id}`,
    cls: `folder${open.has(c.id) ? " open" : ""}`,
    attrs: { cid: String(c.id) },
    html: `<div class="folder-head" data-toggle><span class="chev">›</span><span class="fname" data-name>${esc(c.name)}</span>
        <button class="btn small chatbtn" data-chat ${c.track_ids.length ? "" : "disabled"} title="${c.track_ids.length ? "Chat about every song in this collection" : "Add songs to this collection to chat about it"}">Chat</button>
        <span class="count">${c.track_ids.length}</span><button class="x" data-delete title="Delete this collection (songs stay in your library)">✕</button></div>
      <div class="folder-body"><div class="folder-inner"><div class="folder-list">${rows || `<div class="sub dropmsg">Drop songs here</div>`}</div></div></div>`,
  };
}

export function updateCollections(root: HTMLElement) {
  const inFolder = new Set(S.collections.flatMap((c) => c.track_ids));
  const loose = S.tracks.filter((t) => !inFolder.has(t.id));
  syncCards($("#folders", root), S.collections.map(folderCard));
  syncCards($("#unsorted", root), loose.map((t) => songCard(t, { draggable: true, keyPrefix: "u" })));
  $("#unsorted-title", root).hidden = loose.length === 0 || S.collections.length === 0;
  $("#coll-empty", root).hidden = S.tracks.length > 0 || S.collections.length > 0;
}

export function mountCollections(root: HTMLElement, host: CollHost) {
  const newRow = $("#newrow", root);
  const nameInput = $<HTMLInputElement>("#newname", root);
  const reload = () => host.refresh();

  async function create() {
    const name = nameInput.value.trim();
    if (!name) return;
    await run(async () => { const c = await api.newCollection(name); open.add(c.id); nameInput.value = ""; newRow.hidden = true; await reload(); });
  }

  root.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("[data-new]")) { newRow.hidden = !newRow.hidden; if (!newRow.hidden) nameInput.focus(); return; }
    if (t.closest("[data-create]")) { create(); return; }
    const folder = t.closest<HTMLElement>(".folder");
    const chat = t.closest<HTMLElement>("[data-chat]");
    if (chat && folder) {                                  // Chat sits in the folder's banner but must not open or close the folder
      if (!chat.hasAttribute("disabled")) host.onChat(Number(folder.dataset.cid));
      return;
    }
    const del = t.closest<HTMLElement>("[data-delete]");
    if (del && folder) {                                   // two clicks: songs are safe, but a folder's grouping is not
      if (del.dataset.armed) { run(async () => { await api.deleteCollection(Number(folder.dataset.cid)); await reload(); }); return; }
      del.dataset.armed = "1"; del.textContent = "Delete?"; del.classList.add("armed");
      setTimeout(() => { delete del.dataset.armed; del.textContent = "✕"; del.classList.remove("armed"); }, 3000);
      return;
    }
    const rm = t.closest<HTMLElement>("[data-remove]");
    if (rm) { run(async () => { await api.removeFromCollection(Number(rm.dataset.cid), rm.dataset.remove!); await reload(); }); return; }
    const mini = t.closest<HTMLElement>(".mini");
    if (mini?.dataset.track) { host.onOpen(mini.dataset.track); return; }
    if (t.closest("[data-toggle]") && folder) {
      const id = Number(folder.dataset.cid);
      const now = !open.has(id);
      now ? open.add(id) : open.delete(id);
      folder.classList.toggle("open", now);                // toggled in place so the height eases
      return;
    }
    const card = t.closest<HTMLElement>("#unsorted .card.click");
    if (card?.dataset.track) host.onOpen(card.dataset.track);
  });
  nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); if (e.key === "Escape") { newRow.hidden = true; nameInput.value = ""; } });

  // rename: double-click the name
  root.addEventListener("dblclick", (e) => {
    const nameEl = (e.target as HTMLElement).closest<HTMLElement>("[data-name]");
    const folder = nameEl?.closest<HTMLElement>(".folder");
    if (!nameEl || !folder) return;
    const id = Number(folder.dataset.cid);
    const input = document.createElement("input");
    input.className = "field rename"; input.value = nameEl.textContent ?? ""; input.maxLength = 40;
    nameEl.replaceWith(input); input.focus(); input.select();
    const finish = async (save: boolean) => {
      const v = input.value.trim();
      if (save && v && v !== nameEl.textContent) { try { await api.renameCollection(id, v); } catch (err) { toast(errorText(err)); } }
      await reload();
    };
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") finish(true); if (ev.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true), { once: true });
  });

  // drag a song onto a folder
  root.addEventListener("dragstart", (e) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[draggable=true]");
    if (!el?.dataset.track || !e.dataTransfer) return;
    e.dataTransfer.setData("text/plain", el.dataset.track);
    e.dataTransfer.effectAllowed = "copy";
    el.classList.add("dragging");
  });
  root.addEventListener("dragend", (e) => (e.target as HTMLElement).classList?.remove("dragging"));
  const overFolder = (e: DragEvent) => (e.target as HTMLElement).closest<HTMLElement>(".folder");
  root.addEventListener("dragover", (e) => { const f = overFolder(e); if (!f) return; e.preventDefault(); root.querySelectorAll(".folder.drop").forEach((x) => x !== f && x.classList.remove("drop")); f.classList.add("drop"); });
  root.addEventListener("dragleave", (e) => { const f = overFolder(e); if (f && !f.contains(e.relatedTarget as Node)) f.classList.remove("drop"); });
  root.addEventListener("drop", (e) => {
    const f = overFolder(e);
    if (!f) return;
    e.preventDefault();
    f.classList.remove("drop");
    const trackId = e.dataTransfer?.getData("text/plain");
    if (!trackId) return;
    const cid = Number(f.dataset.cid);
    open.add(cid);
    run(async () => { await api.addToCollection(cid, trackId); await reload(); f.classList.add("bump"); setTimeout(() => f.classList.remove("bump"), 500); });
  });
}
