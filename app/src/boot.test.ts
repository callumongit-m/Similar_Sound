// Boots the real page against a fake service. Catches startup crashes (the window renders nothing) that
// typechecking cannot: e.g. attaching an observer to an element before the shell has created it.
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve()) }));
vi.mock("@tauri-apps/api/dpi", () => ({ LogicalSize: class { constructor(public width: number, public height: number) {} } }));
const setSize = vi.fn();
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ setSize, close: vi.fn() }) }));

const observed: unknown[] = [];
class FakeResizeObserver {
  constructor(public cb: () => void) {}
  observe(el: unknown) {
    if (!(el instanceof Element)) throw new TypeError("ResizeObserver.observe must be an instance of Element"); // same as WebKit
    observed.push(el);
  }
}

const TRACK = { id: "t1", title: "Temptation", artist: "obp", source: "https://soundcloud.com/obpofficial/temptation-5", bpm: 128, key: "C min" };
let jobs: unknown[] = [];

function fakeService() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("http://127.0.0.1:8765", "");
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
    if (path === "/status") return json({ state: "idle", jobs });
    if (path === "/tracks") return json([TRACK]);
    if (path === "/jobs" && init?.method === "POST") return json({ id: 1 });
    return { ok: false, status: 404, json: async () => ({ detail: "nope" }) };
  }));
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = '<div id="app"></div>';
  jobs = [];
  observed.length = 0;
  setSize.mockClear();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  fakeService();
});

test("the page boots: shell, input and size observer exist (startup must not throw)", async () => {
  await import("./main");
  await tick();
  expect(document.querySelector<HTMLInputElement>("#url")?.placeholder).toBe("Paste a SoundCloud link");
  expect(observed).toContain(document.querySelector("#content"));
});

test("a finished track shows name, artist and its bpm / key chips", async () => {
  await import("./main");
  await tick();
  const card = document.querySelector("#list .card");
  expect(card?.querySelector(".title")?.textContent).toBe("Temptation");
  expect(card?.querySelector(".sub")?.textContent).toBe("obp");
  expect([...card!.querySelectorAll(".chip")].map((c) => c.textContent)).toEqual(["128 bpm", "C min"]);
});

test("an analysing link shows the real name and artist (never the URL slug) plus its stage", async () => {
  jobs = [{ id: 1, source: "https://soundcloud.com/x/some-song-5?si=abc", status: "running", stage: "stems", error: null, title: "Some Song", artist: "Someone", track_key: "k1" }];
  await import("./main");
  await tick();
  const card = [...document.querySelectorAll("#list .card")].find((c) => c.querySelector(".status.running"))!;
  expect(card.querySelector(".title")?.textContent).toBe("Some Song");
  expect(card.querySelector(".sub")?.textContent).toBe("Someone");
  expect(card.textContent).toContain("Splitting stems");
  expect(card.textContent).not.toMatch(/\b5\b/);
});

test("before the real title arrives the fallback drops SoundCloud's numeric slug suffix", async () => {
  jobs = [{ id: 1, source: "https://soundcloud.com/obpofficial/temptation-5?si=abc", status: "pending", stage: "", error: null, title: null, artist: null, track_key: "k2" }];
  await import("./main");
  await tick();
  expect(document.querySelector("#list .card .title")?.textContent).toBe("temptation");
});

test("a job that becomes a track updates the same card instead of adding a second", async () => {
  jobs = [{ id: 1, source: "https://soundcloud.com/obpofficial/temptation-5", status: "running", stage: "embed", error: null, title: "Temptation", artist: "obp", track_key: "t1" }];
  await import("./main");
  await tick();
  expect(document.querySelectorAll("#list .card")).toHaveLength(1);   // the job card; the track merges into it
  jobs = [{ id: 1, source: "https://soundcloud.com/obpofficial/temptation-5", status: "done", stage: "", error: null, title: "Temptation", artist: "obp", track_key: "t1" }];
  await tick(1400);                                                    // next poll
  expect(document.querySelectorAll("#list .card")).toHaveLength(1);
  expect(document.querySelector("#list .card")?.querySelectorAll(".chip")).toHaveLength(2);
});
