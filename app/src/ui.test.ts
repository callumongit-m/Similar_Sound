import { expect, test } from "vitest";
import { dragging, syncCards, type Card } from "./ui";

const card = (draggable: boolean): Card => ({ key: "u-1", html: "<b>Song</b>", cls: "click", attrs: { track: "t1" }, draggable });

test("syncing an unchanged list touches nothing, so a drag in progress is never cancelled", async () => {
  const box = document.createElement("div");
  syncCards(box, [card(true)]);
  box.querySelector(".card")!.classList.remove("enter");
  box.querySelector(".card")!.classList.add("dragging");          // the view marks the card while it is dragged
  const seen: string[] = [];
  const obs = new MutationObserver((m) => m.forEach((r) => seen.push(`${r.type}:${r.attributeName ?? ""}`)));
  obs.observe(box, { attributes: true, childList: true, subtree: true, characterData: true });
  syncCards(box, [card(true)]);
  await new Promise((r) => setTimeout(r, 0));
  expect(seen).toEqual([]);
  expect(box.querySelector(".card")!.classList.contains("dragging")).toBe(true);
});

test("a real change still updates, and lists hold still while a drag is active", () => {
  const box = document.createElement("div");
  syncCards(box, [card(true)]);
  syncCards(box, [card(false)]);
  expect((box.querySelector(".card") as HTMLElement).draggable).toBe(false);
  dragging.active = true;
  syncCards(box, []);
  expect(box.querySelectorAll(".card").length).toBe(1);            // untouched mid-drag
  dragging.active = false;
  syncCards(box, []);
});
