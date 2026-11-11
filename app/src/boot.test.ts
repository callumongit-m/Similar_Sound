// Boots the real page against a fake service. Catches startup crashes (the window renders nothing) and
// checks the three parts behave: Add song, Collections, Compare my project.
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve()) }));
vi.mock("@tauri-apps/api/dpi", () => ({ LogicalSize: class { constructor(public width: number, public height: number) {} } }));
const setSize = vi.fn();
const setPosition = vi.fn();
const screen = { y: 100, height: 1000 };                                     // where the window is, and how tall the screen is (physical px)
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ setSize, close: vi.fn(), setPosition, outerPosition: async () => ({ x: 50, y: screen.y }) }),
  currentMonitor: async () => ({ position: { x: 0, y: 0 }, size: { width: 2000, height: screen.height }, workArea: { position: { x: 0, y: 0 }, size: { width: 2000, height: screen.height } }, scaleFactor: 1 }),
  PhysicalPosition: class { constructor(public x: number, public y: number) {} },
}));

const observed: unknown[] = [];
class FakeResizeObserver {
  static all: FakeResizeObserver[] = [];
  constructor(public cb: () => void) { FakeResizeObserver.all.push(this); }
  observe(el: unknown) {
    if (!(el instanceof Element)) throw new TypeError("ResizeObserver.observe must be an instance of Element"); // same as WebKit
    observed.push(el);
  }
}

const T1 = { id: "t1", title: "Temptation", artist: "obp", source: "https://soundcloud.com/obpofficial/temptation-5", bpm: 128, key: "C min" };
const T2 = { id: "t2", title: "Free", artist: "Joss Dean", source: "https://soundcloud.com/x/free", bpm: 126, key: "A min" };
const DOC = { ...T1, segments: { build: [120, 150], drop: [150, 180] }, alternative_drops: [], groove: { swing: 0.12 } };
const FEEL = { weakest: "other", pairs: 1, reliable: false, note: "Add more songs for a sharper read: with only 1 pair of reference songs to measure against, this is a rough guide.",
  aspects: [
    { kind: "mix", label: "Overall vibe", score: 80, typical: 82, gap: 2, pairs: 1, scope: "these songs", closest: { title: "Temptation", score: 84 }, furthest: { title: "Free", score: 76 } },
    { kind: "groove", label: "Groove", score: 70, typical: 77, gap: 7, pairs: 1, scope: "these songs", closest: null, furthest: null },
    { kind: "drums", label: "Drums", score: 86, typical: 85, gap: -1, pairs: 1, scope: "these songs", closest: null, furthest: null },
    { kind: "bass", label: "Bass", score: 80, typical: 86, gap: 6, pairs: 1, scope: "these songs", closest: null, furthest: null },
    { kind: "other", label: "Synths", score: 41, typical: 65, gap: 24, pairs: 1, scope: "these songs", closest: null, furthest: null }] };
const WHY = { other: [{ label: "Synths/other · top level (dB)", text: "Synths/other · top level (dB): yours -11.9; UK minimal averages -44.7", delta: 33, outside: true }], mix: [], groove: [], drums: [], bass: [] };
const GAPS = { feel: FEEL, why: WHY, reference: "UK minimal", n: 1, is_collection: true, small: true, note: "UK minimal only has 1 song, so treat this as a rough guide.", references: ["Temptation"],
  gaps: [{ label: "Bass · sub level (dB)", yours: -9.1, reference: -5.2, delta: -3.9, outside: true, n: 3, text: "Bass · sub level (dB): yours -9.1; UK minimal averages -5.2, range -6.4 to -3.9 (below all 3)" },
         { label: "Groove · swing", yours: 0, reference: 0.12, delta: -0.12, outside: false, n: 3, text: "Groove · swing: yours 0.00; UK minimal averages 0.12, range 0.10 to 0.14" }] };

interface World { state?: string; nextId?: number; down?: boolean; answerText?: string; llm: any; keyBodies: any[]; jobs: any[]; tracks: any[]; collections: any[]; listen: any; askError: any; calls: { method: string; path: string; body: any }[]; keySaved: boolean }
let W: World;
// The real service gives a job the same track_key from the moment it is created, so one card can follow it.
const keyFor = (url: string) => (url.endsWith("/free") ? "t2" : "k:" + url.split("/").pop()!.split("?")[0]);
const NO_KEY = { configured: false, provider: null, name: null, model: null };
const PROVIDERS = [{ id: "gemini", name: "Google Gemini", needs_address: false }, { id: "mistral", name: "Mistral", needs_address: false }, { id: "custom", name: "Other (OpenAI-compatible)", needs_address: true }];

function fakeService() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (W.down) throw new TypeError("Load failed");                       // the service is still starting
    const path = url.replace("http://127.0.0.1:8765", "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    W.calls.push({ method, path, body });
    const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
    const fail = (status: number, detail: unknown) => ({ ok: false, status, json: async () => ({ detail }) });
    if (path === "/status") return ok({ state: W.state ?? "idle", jobs: W.jobs });
    if (path === "/pause") { W.state = "paused_user"; return ok({ state: W.state }); }
    if (path === "/resume") { W.state = "working"; W.jobs.forEach((j) => { if (j.status === "pending") j.status = "running"; }); return ok({ state: W.state }); }
    if (path === "/tracks") return ok(W.tracks);
    if (path === "/collections" && method === "GET") return ok(W.collections);
    if (path === "/collections" && method === "POST") { const c = { id: 9, name: body.name, track_ids: [] }; W.collections.push(c); return ok(c); }
    if (/^\/collections\/\d+\/tracks$/.test(path) && method === "POST") { W.collections.find((c) => c.id === Number(path.split("/")[2]))!.track_ids.push(body.track_id); return ok({}); }
    if (path === "/jobs" && method === "POST") {
      const id = (W.nextId = (W.nextId ?? 4) + 1);
      W.jobs.unshift({ id, source: body.url, status: !W.state || W.state === "idle" ? "running" : "pending", stage: "stems", error: null, title: body.url.includes("two") ? "Second Song" : "Some Song", artist: "Someone", track_key: keyFor(body.url) });
      return ok({ id });
    }
    if (path === "/tracks/t1") return ok(DOC);
    if (path.startsWith("/tracks/t1/similar")) return ok([]);
    if (path === "/tracks/t1/ask") { if (W.askError) return fail(503, W.askError); return ok({ answer: W.answerText ?? "It is rolling." }); }
    if (path === "/settings" && method === "GET") return ok(W.llm);
    if (path === "/settings/llm" && method === "POST") {
      W.keyBodies.push(body);
      if (body.key.startsWith("zzz") && !body.provider) return fail(400, { message: "I don't recognise that key's company. Pick it from the list.", code: "unknown_provider", providers: PROVIDERS });
      if (body.key.startsWith("bad")) return fail(400, { message: "That key wasn't accepted. Add it again with the key icon.", code: "bad_key", providers: PROVIDERS });
      W.keySaved = true; W.askError = null;
      W.llm = { configured: true, provider: body.provider ?? "openai", name: body.provider ? "Mistral" : "OpenAI", model: "gpt-4.1-nano" };
      return ok(W.llm);
    }
    if (path === "/settings/llm" && method === "DELETE") { W.llm = NO_KEY; return ok(W.llm); }
    if (path === "/listen") return ok(W.listen);
    if (path === "/listen/start") { W.listen = { ...W.listen, state: "waiting" }; return ok(W.listen); }
    if (path === "/listen/cancel") { W.listen = { ...W.listen, state: "idle" }; return ok(W.listen); }
    if (path === "/compare") return ok(GAPS);
    if (path === "/compare/ask") { if (W.askError) return fail(503, W.askError); return ok({ answer: "Fix the bass first." }); }
    return fail(404, "nope");
  }));
}

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const $ = (s: string) => document.querySelector(s) as HTMLElement;
const $$ = (s: string) => [...document.querySelectorAll(s)] as HTMLElement[];
const click = (el: Element) => el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
const tab = (name: string) => click($(`#nav [data-tab="${name}"]`));
const LISTEN = { state: "idle", level: 0, seconds: 0, message: "", ableton_running: true, heard: null };

async function boot() { await import("./main"); await tick(); }

// Each test boots a fresh page, so the previous page's polling timers must die with it, or they keep
// repainting the new test's DOM.
const timers: number[] = [];
const realSetInterval = window.setInterval.bind(window);
window.setInterval = ((fn: TimerHandler, ms?: number, ...a: unknown[]) => { const id = realSetInterval(fn, ms, ...a); timers.push(id); return id; }) as typeof window.setInterval;
afterEach(() => { timers.splice(0).forEach((id) => clearInterval(id)); delete (HTMLElement.prototype as any).offsetHeight; });

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = '<div id="app"></div>';
  observed.length = 0;
  FakeResizeObserver.all.length = 0;
  setSize.mockClear();
  setPosition.mockClear();
  screen.y = 100; screen.height = 1000;
  W = { llm: NO_KEY, keyBodies: [], jobs: [], tracks: [T1, T2], collections: [], listen: { ...LISTEN }, askError: null, calls: [], keySaved: false };
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  fakeService();
});

// ---------------- startup + structure ----------------
test("the page boots with the three parts and the paste bar inside Add song", async () => {
  await boot();
  expect($$("#nav [data-tab]").map((b) => b.textContent)).toEqual(["Add song", "Collections", "Compare my project"]);
  expect($("#pane-add").hidden).toBe(false);
  expect($("#pane-collections").hidden).toBe(true);
  expect(($("#url") as HTMLInputElement).placeholder).toBe("Paste a SoundCloud link");
  expect($("#pane-collections").querySelector("#url")).toBeNull();               // paste bar lives only in Add song
  expect(observed).toContain($("#content"));
});

test("switching parts shows one at a time and slides the highlight", async () => {
  await boot();
  tab("collections");
  expect($("#pane-add").hidden).toBe(true);
  expect($("#pane-collections").hidden).toBe(false);
  expect($("#nav").style.getPropertyValue("--i")).toBe("1");
  tab("compare");
  expect($("#nav").style.getPropertyValue("--i")).toBe("2");
});

// ---------------- Add song ----------------
test("Add song shows nothing from before this session, only what you add now", async () => {
  await boot();
  expect($$("#latest .card")).toHaveLength(0);                                   // library has 2 songs, none shown here
  ($("#url") as HTMLInputElement).value = "https://soundcloud.com/x/some-song-5?si=abc";
  ($("#url") as HTMLInputElement).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await tick(80);
  const card = $("#latest .card");
  expect(card.querySelector(".title")?.textContent).toBe("Some Song");         // the real name, not "some song 5"
  expect(card.querySelector(".sub")?.textContent).toBe("Someone");
  expect(card.textContent).toContain("Splitting stems");
  expect(card.textContent).not.toMatch(/\b5\b/);
  expect(($("#url") as HTMLInputElement).value).toBe("");
});

test("when analysis finishes the same card gets its bpm and key chips", async () => {
  await boot();
  ($("#url") as HTMLInputElement).value = "https://soundcloud.com/x/free";
  ($("#url") as HTMLInputElement).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await tick(80);
  W.jobs = [{ id: 5, source: "https://soundcloud.com/x/free", status: "done", stage: "", error: null, title: "Free", artist: "Joss Dean", track_key: "t2" }];
  await tick(1400);
  expect($$("#latest .card")).toHaveLength(1);
  expect($$("#latest .card .chip").map((c) => c.textContent)).toEqual(["126 bpm", "A min"]);
});

test("the stuck-drop prompt shows the message and a timestamp box", async () => {
  await boot();
  ($("#url") as HTMLInputElement).value = "https://soundcloud.com/x/hard";
  ($("#url") as HTMLInputElement).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await tick(80);
  W.jobs = [{ id: 5, source: "https://soundcloud.com/x/hard", status: "needs_timestamp", stage: "", error: "Sorry my system couldn't identify the main drop on this song, just give me the timestamp and I'll be able to carry on :)", title: "Hard", artist: "A", track_key: "k:hard" }];
  await tick(1400);
  expect($("#latest .card").textContent).toContain("just give me the timestamp");
  expect($("#latest input[data-ts]")).toBeTruthy();
});

// ---------------- Collections ----------------
test("Collections shows folders and the unsorted songs, and dropping a song on a folder adds it", async () => {
  W.collections = [{ id: 1, name: "UK minimal", track_ids: ["t1"] }];
  await boot();
  tab("collections");
  expect($("#folders .folder .fname").textContent).toBe("UK minimal");
  expect($("#folders .folder .count").textContent).toBe("1");
  expect($$("#unsorted .card").map((c) => c.querySelector(".title")?.textContent)).toEqual(["Free"]);   // t1 is in a folder
  expect($("#unsorted .card").draggable).toBe(true);
  const drop = new Event("drop", { bubbles: true, cancelable: true }) as any;
  drop.dataTransfer = { getData: () => "t2" };
  $("#folders .folder").dispatchEvent(drop);
  await tick(80);
  expect(W.calls.some((c) => c.method === "POST" && c.path === "/collections/1/tracks" && c.body.track_id === "t2")).toBe(true);
  expect($("#folders .folder .count").textContent).toBe("2");
  expect($$("#unsorted .card:not(.leaving)")).toHaveLength(0);                              // it fades out of Unsorted
});

test("a song can be dropped into a second collection too", async () => {
  W.collections = [{ id: 1, name: "A", track_ids: ["t1"] }, { id: 2, name: "B", track_ids: [] }];
  await boot();
  tab("collections");
  const drop = new Event("drop", { bubbles: true, cancelable: true }) as any;
  drop.dataTransfer = { getData: () => "t1" };
  $$("#folders .folder")[1].dispatchEvent(drop);
  await tick(80);
  expect(W.collections.map((c) => c.track_ids)).toEqual([["t1"], ["t1"]]);
});

test("creating a collection", async () => {
  await boot();
  tab("collections");
  click($("[data-new]"));
  ($("#newname") as HTMLInputElement).value = "Rolling bass";
  click($("[data-create]"));
  await tick(80);
  expect(W.calls.some((c) => c.method === "POST" && c.path === "/collections" && c.body.name === "Rolling bass")).toBe(true);
  expect($("#folders .folder .fname").textContent).toBe("Rolling bass");
});

test("an empty library shows a hint instead of a blank pane", async () => {
  W.tracks = [];
  await boot();
  tab("collections");
  expect($("#coll-empty").hidden).toBe(false);
});

// ---------------- song detail ----------------
test("opening a song shows only BPM, Key and Swing as chips", async () => {
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  const labels = $$("#detail .chip.big").map((c) => c.textContent);
  expect(labels).toEqual(["BPM128", "KeyC min", "Swing+12%"]);
  expect($("#detail .askbox")).toBeTruthy();
});

// ---------------- Compare my project ----------------
test("Compare starts listening and shows the orb waiting for audio", async () => {
  await boot();
  tab("compare");
  await tick(80);
  expect(W.calls.some((c) => c.path === "/listen/start")).toBe(true);
  expect($("#cmp .orb.s-waiting")).toBeTruthy();
  expect($("#cmp .cmp-title").textContent).toBe("Waiting for audio");
});

test("the orb reacts to the level, then asks what to compare with, then shows the differences", async () => {
  await boot();
  tab("compare");
  await tick(80);
  W.listen = { ...LISTEN, state: "listening", level: 0.5, seconds: 7 };
  await tick(400);
  expect($("#cmp .cmp-title").textContent).toBe("Listening");
  expect($("#cmp .orb").style.getPropertyValue("--level")).toBe("0.5");
  W.listen = { ...W.listen, seconds: 19 };
  await tick(400);
  expect($("#cmp .cmp-sub").textContent).toBe("");                                 // no running seconds: it isn't the song's clock
  expect($("#cmp").textContent).not.toMatch(/\d+s\b/);
  W.collections = [{ id: 1, name: "UK minimal", track_ids: ["t1"] }];
  W.listen = { ...LISTEN, state: "ready", heard: { bpm: 124, key: "A min", seconds: 22 } };
  await tick(400);
  expect($("#cmp .cmp-title").textContent).toBe("Select the Song or Collection you'd like to compare with");
  expect($("#cmp .cmp-sub").textContent).toContain("124 bpm");
  click($("#cmp .pick[data-kind='collection']"));
  await tick(80);
  expect(W.calls.find((c) => c.path === "/compare")!.body).toEqual({ kind: "collection", id: 1 });
  expect($("#cmp .cmp-head").textContent).toContain("UK minimal");
  expect($("#cmp .answer.notice").textContent).toContain("rough guide");           // small collection warning
  expect($$("#cmp .gap")).toHaveLength(2);
  expect($("#cmp .gap.out .gl").textContent).toBe("Bass · sub level (dB)");
  expect($("#cmp .askbox")).toBeTruthy();
});

test("leaving Compare while it is only waiting stops listening", async () => {
  await boot();
  tab("compare");
  await tick(80);
  tab("add");
  await tick(80);
  expect(W.calls.some((c) => c.path === "/listen/cancel")).toBe(true);
});

// ---------------- ask: key + credits ----------------
test("running out of credits shows exactly 'I need more token credits'", async () => {
  W.askError = { message: "I need more token credits", code: "credits" };
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  ($("#detail [data-q]") as HTMLTextAreaElement).value = "why?";
  click($("#detail [data-ask]"));
  await tick(80);
  expect($("#detail .answer").textContent).toBe("I need more token credits");
  expect($("#detail .answer").classList.contains("notice")).toBe(true);
});

test("without a key the ask box points at the key icon and its button opens the key panel", async () => {
  W.askError = { message: "You need to add your own AI API key using the key icon in the top left", code: "no_key" };
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  ($("#detail [data-q]") as HTMLTextAreaElement).value = "why?";
  click($("#detail [data-ask]"));
  await tick(80);
  expect($("#detail .answer").textContent).toContain("You need to add your own AI API key using the key icon in the top left");
  expect($("#keypanel").classList.contains("open")).toBe(false);
  click($("#detail [data-openkey]"));
  expect($("#keypanel").classList.contains("open")).toBe(true);
});

// ---------------- the key icon ----------------
const key = (v: string) => { ($("#keypanel [data-key]") as HTMLInputElement).value = v; click($("#keypanel [data-save]")); };

test("the key icon is top left, dim until a key is connected, and opens a password field", async () => {
  await boot();
  expect($(".titlebar").firstElementChild!.id).toBe("keybtn");
  expect($("#keybtn").classList.contains("on")).toBe(false);
  click($("#keybtn"));
  expect($("#keypanel").classList.contains("open")).toBe(true);
  expect(($("#keypanel [data-key]") as HTMLInputElement).type).toBe("password");
  expect($("#keypanel [data-status]").textContent).toContain("No key yet");
  click($("#keybtn"));
  expect($("#keypanel").classList.contains("open")).toBe(false);
});

test("any company's key: saved without asking which company, shown back as the company, cleared from the page", async () => {
  await boot();
  click($("#keybtn"));
  const secret = "sk-proj-" + "q".repeat(40);
  key(secret);
  await tick(80);
  expect(W.keyBodies).toEqual([{ key: secret }]);                       // no provider chosen by the user: the service detects it
  expect($("#keypanel [data-msg]").textContent).toBe("Connected.");
  expect($("#keypanel [data-status]").textContent).toContain("OpenAI");
  expect($("#keypanel [data-status]").textContent).toContain("gpt-4.1-nano");
  expect(($("#keypanel [data-key]") as HTMLInputElement).value).toBe("");
  expect(document.body.innerHTML).not.toContain("q".repeat(40));        // the key is not left anywhere in the page
  expect($("#keybtn").classList.contains("on")).toBe(true);
});

test("a key it can't place asks which company, then saves it", async () => {
  await boot();
  click($("#keybtn"));
  key("zzz" + "w".repeat(30));
  await tick(80);
  expect($("#keypanel [data-msg]").textContent).toContain("don't recognise");
  const more = $("#keypanel [data-more]");
  expect(more.hidden).toBe(false);
  expect([...more.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Google Gemini", "Mistral", "Other (OpenAI-compatible)"]);
  expect(more.querySelector<HTMLElement>("[data-address-row]")!.hidden).toBe(true);     // only "Other" needs an address
  (more.querySelector("select") as HTMLSelectElement).value = "custom";
  more.querySelector("select")!.dispatchEvent(new Event("change"));
  expect(more.querySelector<HTMLElement>("[data-address-row]")!.hidden).toBe(false);
  expect(more.querySelector<HTMLElement>("[data-model-row]")!.hidden).toBe(false);
  (more.querySelector("select") as HTMLSelectElement).value = "mistral";
  more.querySelector("select")!.dispatchEvent(new Event("change"));
  click($("#keypanel [data-save]"));
  await tick(80);
  expect(W.keyBodies.at(-1)).toEqual({ key: "zzz" + "w".repeat(30), provider: "mistral" });
  expect($("#keypanel [data-status]").textContent).toContain("Mistral");
});

test("a rejected key shows the reason and nothing is connected", async () => {
  await boot();
  click($("#keybtn"));
  key("bad" + "e".repeat(30));
  await tick(80);
  expect($("#keypanel [data-msg]").textContent).toContain("wasn't accepted");
  expect($("#keypanel [data-msg]").classList.contains("err")).toBe(true);
  expect($("#keybtn").classList.contains("on")).toBe(false);
});

test("a connected key can be removed", async () => {
  W.llm = { configured: true, provider: "anthropic", name: "Anthropic Claude", model: "claude-haiku-5-5" };
  await boot();
  expect($("#keybtn").classList.contains("on")).toBe(true);
  click($("#keybtn"));
  expect($("#keypanel [data-status]").textContent).toContain("Anthropic Claude");
  click($("#keypanel [data-remove]"));
  await tick(80);
  expect($("#keybtn").classList.contains("on")).toBe(false);
  expect($("#keypanel [data-status]").textContent).toContain("No key yet");
});


// ---------------- ask: Enter sends ----------------
const enter = (el: Element, init: KeyboardEventInit = {}) => {
  const e = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true, ...init });
  el.dispatchEvent(e);
  return e;
};

test("pressing Enter in the ask box sends the question, with no click on Ask", async () => {
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  const q = $("#detail [data-q]") as HTMLTextAreaElement;
  q.value = "what makes the groove roll?";
  const e = enter(q);
  await tick(80);
  expect(W.calls.some((c) => c.path === "/tracks/t1/ask" && c.body.question === "what makes the groove roll?")).toBe(true);
  expect(e.defaultPrevented).toBe(true);                         // no stray newline left behind
  expect($("#detail .answer").textContent).toBe("It is rolling.");
  expect(q.value).toBe("");                                      // cleared, ready for the next question
});

test("Shift+Enter makes a new line instead of sending, and an empty box sends nothing", async () => {
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  const q = $("#detail [data-q]") as HTMLTextAreaElement;
  q.value = "first line";
  const e = enter(q, { shiftKey: true });
  await tick(60);
  expect(e.defaultPrevented).toBe(false);
  expect(W.calls.some((c) => c.path === "/tracks/t1/ask")).toBe(false);
  q.value = "   ";
  enter(q);
  await tick(60);
  expect(W.calls.some((c) => c.path === "/tracks/t1/ask")).toBe(false);
});

test("Enter works in the comparison's ask box too", async () => {
  await boot();
  tab("compare");
  await tick(80);
  W.listen = { ...LISTEN, state: "ready", heard: { bpm: 124, key: "A min", seconds: 22 } };
  await tick(400);
  click($("#cmp .pick[data-kind='track']"));
  await tick(80);
  const q = $("#cmp [data-q]") as HTMLTextAreaElement;
  q.value = "what should I fix first?";
  enter(q);
  await tick(80);
  expect(W.calls.some((c) => c.path === "/compare/ask" && c.body.question === "what should I fix first?")).toBe(true);
  expect($("#cmp [data-answer] .answer").textContent).toBe("Fix the bass first.");
});


// ---------------- ask: formatting + the caveat ----------------
const CAVEAT = "Each song only has data for the main build up and drop.";

test("every 'ask about' heading carries the caveat about what data a song has", async () => {
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  const heading = [...document.querySelectorAll("#detail .section")].find((h) => h.textContent === "Ask about this track")!;
  expect(heading.nextElementSibling!.textContent).toBe(CAVEAT);
  expect(heading.nextElementSibling!.classList.contains("section-sub")).toBe(true);
});

test("the comparison's ask heading has the same caveat", async () => {
  await boot();
  tab("compare");
  await tick(80);
  W.listen = { ...LISTEN, state: "ready", heard: { bpm: 124, key: "A min", seconds: 22 } };
  await tick(400);
  click($("#cmp .pick[data-kind='track']"));
  await tick(80);
  const heading = [...document.querySelectorAll("#cmp .section")].find((h) => /^Ask about this/.test(h.textContent ?? ""))!;
  expect(heading.textContent).toBe("Ask about this comparison");
  expect(heading.nextElementSibling!.textContent).toBe(CAVEAT);
});

test("**bold** in an answer is shown bold, with the numbered list kept and nothing injected", async () => {
  W.answerText = "To recreate it:\n1. **Keep it simple:** one oscillator.\n2. **Focus the frequency:** cut below 250 Hz.\n- a bullet\n<img src=x onerror=alert(1)> and **unfinished";
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
  ($("#detail [data-q]") as HTMLTextAreaElement).value = "wavetable?";
  click($("#detail [data-ask]"));
  await tick(80);
  const a = $("#detail [data-answer] .answer");
  expect([...a.querySelectorAll("strong")].map((s) => s.textContent)).toEqual(["Keep it simple:", "Focus the frequency:"]);
  expect(a.textContent).toContain("1. Keep it simple: one oscillator.");        // numbering survives, asterisks are gone
  expect(a.textContent).toContain("• a bullet");
  expect(a.textContent).toContain("**unfinished");                              // an unmatched ** is left alone, not eaten
  expect(a.querySelector("img")).toBeNull();                                    // the reply cannot inject markup
  expect(a.innerHTML).toContain("&lt;img");
});


// ---------------- conversation ----------------
async function openAsk() {
  await boot();
  tab("collections");
  click($("#unsorted .card.click"));
  await tick(80);
}
const say = async (text: string) => { ($("#detail [data-q]") as HTMLTextAreaElement).value = text; enter($("#detail [data-q]")); await tick(80); };
const asks = () => W.calls.filter((c) => c.path === "/tracks/t1/ask");

test("a follow-up carries the conversation so far, and the thread keeps every question and answer", async () => {
  await openAsk();
  W.answerText = "The bass is -10.75 dB.";
  await say("how loud is the bass?");
  W.answerText = "The drums are -10 dB.";
  await say("and the drums?");
  expect(asks()[0].body).toEqual({ question: "how loud is the bass?", history: [] });
  expect(asks()[1].body).toEqual({ question: "and the drums?", history: [{ q: "how loud is the bass?", a: "The bass is -10.75 dB." }] });
  expect($$("#detail .turn .q").map((q) => q.textContent)).toEqual(["how loud is the bass?", "and the drums?"]);
  expect($$("#detail .turn .answer").map((a) => a.textContent)).toEqual(["The bass is -10.75 dB.", "The drums are -10 dB."]);
});

test("a question that failed is shown but is not part of the conversation sent next time", async () => {
  await openAsk();
  W.answerText = "First answer.";
  await say("one");
  W.askError = { message: "I need more token credits", code: "credits" };
  await say("two (fails)");
  expect($$("#detail .turn .answer").map((a) => a.textContent)).toEqual(["First answer.", "I need more token credits"]);
  W.askError = null; W.answerText = "Third answer.";
  await say("three");
  expect(asks()[2].body.history).toEqual([{ q: "one", a: "First answer." }]);        // "two" never got an answer, so it isn't history
});

test("New conversation starts fresh", async () => {
  await openAsk();
  expect($("#detail [data-newchat]").hidden).toBe(true);
  await say("one");
  expect($("#detail [data-newchat]").hidden).toBe(false);
  click($("#detail [data-newchat]"));
  expect($$("#detail .turn")).toHaveLength(0);
  expect($("#detail [data-newchat]").hidden).toBe(true);
  await say("fresh start");
  expect(asks().at(-1)!.body.history).toEqual([]);
});

test("pressing Enter twice quickly does not send two overlapping questions", async () => {
  await openAsk();
  const q = $("#detail [data-q]") as HTMLTextAreaElement;
  q.value = "first"; enter(q);
  q.value = "second, while the first is still thinking"; enter(q);
  await tick(80);
  expect(asks()).toHaveLength(1);
});

// ---------------- how it feels ----------------
async function compareToCollection() {
  W.collections = [{ id: 1, name: "UK minimal", track_ids: ["t1"] }];
  await boot();
  tab("compare");
  await tick(80);
  W.listen = { ...LISTEN, state: "ready", heard: { bpm: 124, key: "A min", seconds: 22 } };
  await tick(400);
  click($("#cmp .pick[data-kind='collection']"));
  await tick(80);
}

test("the comparison shows how it feels, ranks the furthest aspect and explains it with the measured differences", async () => {
  await compareToCollection();
  const rows = $$("#cmp .feelrow");
  expect(rows.map((r) => r.querySelector(".fr-label")!.textContent)).toEqual(["Overall vibe", "Groove", "Drums", "Bass", "Synths"]);
  expect(rows.map((r) => r.classList.contains("weak"))).toEqual([false, false, false, false, true]);
  const weak = rows[4];
  expect(weak.querySelector(".fr-num")!.textContent).toContain("41");
  expect(weak.querySelector(".fr-num")!.textContent).toContain("typical 65");
  expect(weak.textContent).toContain("Furthest from the feel.");
  expect(weak.textContent).toContain("Synths/other · top level (dB): yours -11.9");
  expect(weak.querySelector<HTMLElement>(".fill")!.style.getPropertyValue("--v")).toBe("41%");
  expect(weak.querySelector<HTMLElement>(".tick")!.style.getPropertyValue("--t")).toBe("65%");
  expect(rows[0].textContent).toContain("closest Temptation 84 · furthest Free 76");
  expect(rows[2].querySelector(".fr-sub")!.textContent).toBe("as close as typical");                 // -1 is not further than typical
  expect(rows[1].querySelector(".fr-sub")!.textContent).toBe("7 further than typical");
});

test("with too few songs the comparison says the read of the feel is rough", async () => {
  await compareToCollection();
  expect($("#cmp .feel").previousElementSibling!.textContent).toContain("1 pair of reference songs");
});

test("how it feels comes before the list of measured differences", async () => {
  await compareToCollection();
  const sections = $$("#cmp .section").map((s) => s.textContent);
  expect(sections.indexOf("How it feels")).toBeLessThan(sections.indexOf("Where your project differs"));
});


// ---------------- key: a close call shows both keys ----------------
test("a close key call shows both keys on the song and in what the project heard", async () => {
  W.tracks = [{ ...T1, key: "B min", key_alternatives: ["E min"] }, T2];
  await boot();
  tab("collections");
  expect($$("#unsorted .card").map((c) => [...c.querySelectorAll(".chip")].map((x) => x.textContent).join("|"))).toContain("128 bpm|B min / E min");
  expect($$("#unsorted .card").map((c) => [...c.querySelectorAll(".chip")].map((x) => x.textContent).join("|"))).toContain("126 bpm|A min");   // a clear key stays single
  tab("compare");
  await tick(80);
  W.listen = { ...LISTEN, state: "ready", heard: { bpm: 124, key: "B min", key_alternatives: ["E min"], seconds: 22 } };
  await tick(400);
  expect($("#cmp .cmp-sub").textContent).toContain("B min / E min");
});


// ---------------- comparison: the two ways back ----------------
const compareEvents = () => W.calls.filter((c) => c.path === "/compare" || c.path === "/listen/start").map((c) => c.path);

test("under 'Compared with' come the two links, in that order, and the old bottom button is gone", async () => {
  await compareToCollection();
  const head = $("#cmp .cmp-head");
  expect(head.textContent).toContain("Compared with UK minimal");
  const links = head.nextElementSibling!;
  expect(links.classList.contains("cmp-links")).toBe(true);
  expect([...links.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
    "Compare to a different collection or song →", "Listen to my project again →"]);
  expect($$("#cmp [data-again]")).toHaveLength(1);                          // only the link, no second button at the bottom
});

test("'Compare to a different collection or song' goes back to the picker without listening again", async () => {
  await compareToCollection();
  click($("#cmp [data-rechoose]"));
  await tick(400);
  expect($("#cmp .cmp-title").textContent).toBe("Select the Song or Collection you'd like to compare with");
  expect($$("#cmp .pick").length).toBeGreaterThan(0);
  expect(W.calls.filter((c) => c.path === "/listen/start")).toHaveLength(1);        // only the initial listen
  click($("#cmp .pick[data-kind='track']"));                                         // pick a song this time
  await tick(120);
  expect(W.calls.filter((c) => c.path === "/compare").map((c) => c.body.kind)).toEqual(["collection", "track"]);
  expect($("#cmp .cmp-head").textContent).toContain("Compared with");
  expect($$("#cmp .turn")).toHaveLength(0);                                          // a new comparison starts a fresh conversation
});

test("'Listen to my project again' re-listens from scratch", async () => {
  await compareToCollection();
  click($("#cmp [data-again]"));
  await tick(120);
  expect(W.calls.filter((c) => c.path === "/listen/start")).toHaveLength(2);
  expect($("#cmp .cmp-title").textContent).toBe("Waiting for audio");
  expect($("#cmp .orb.s-waiting")).toBeTruthy();
});

test("coming back to Compare shows the earlier comparison, with both links, until you choose otherwise", async () => {
  await compareToCollection();
  tab("add");
  await tick(80);
  W.listen = { ...W.listen, state: "ready" };
  tab("compare");
  await tick(120);
  expect($("#cmp .cmp-head").textContent).toContain("Compared with UK minimal");
  expect($$("#cmp .cmp-links button")).toHaveLength(2);
});


// ---------------- ghost loading while the service starts ----------------
import { readFileSync } from "node:fs";

test("while the service starts the window shows a phasing ghost, not 'Service not running'", async () => {
  W.down = true;
  await boot();
  expect(document.body.classList.contains("booting")).toBe(true);
  expect($("#offline").hidden).toBe(true);
  expect($$("#pane-add .ghostlist .card.ghost")).toHaveLength(1);                       // a ghost of the song card to come
  expect($$("#pane-collections .ghostlist .card.ghost")).toHaveLength(3);               // and of the library
  expect($$("#pane-add .ghost .gline, #pane-add .ghost .gchip").length).toBeGreaterThan(2);
  const url = $("#url") as HTMLInputElement;
  expect(url.disabled).toBe(true);
  expect(url.placeholder).toBe("Getting ready…");
});

test("the ghost gives way to the real window the moment the service answers", async () => {
  W.down = true;
  await boot();
  expect(document.body.classList.contains("booting")).toBe(true);
  W.down = false;
  await tick(1500);                                                                      // the next poll
  expect(document.body.classList.contains("booting")).toBe(false);
  const url = $("#url") as HTMLInputElement;
  expect(url.disabled).toBe(false);
  expect(url.placeholder).toBe("Paste a SoundCloud link");
  expect($("#offline").hidden).toBe(true);
});

test("Compare shows a ghost orb while starting, then starts listening once the service is up", async () => {
  W.down = true;
  await boot();
  tab("compare");
  await tick(80);
  expect($("#pane-compare .ghostlist .gorb")).toBeTruthy();
  expect($("#pane-compare .ghostlist").textContent).toContain("Getting ready…");
  expect(W.calls.some((c) => c.path.startsWith("/listen"))).toBe(false);                 // nothing is asked of a service that isn't there
  W.down = false;
  await tick(1600);
  expect(W.calls.some((c) => c.path === "/listen/start")).toBe(true);                    // and it picks up where it should
  expect($("#cmp .cmp-title").textContent).toBe("Waiting for audio");
});

test("if the service never comes up, after a grace period it says so (and only then)", async () => {
  W.down = true;
  const realNow = Date.now();
  const spy = vi.spyOn(Date, "now").mockReturnValue(realNow);
  await boot();
  await tick(1400);
  expect($("#offline").hidden).toBe(true);                                                // still within the grace period
  spy.mockReturnValue(realNow + 31_000);
  await tick(1400);
  expect(document.body.classList.contains("booting")).toBe(false);
  expect($("#offline").hidden).toBe(false);
  expect($("#offline").textContent).toContain("Service not running");
  spy.mockRestore();
});

// ---------------- the corner buttons follow the window's corner ----------------
test("corner buttons use the concentric radius, and the CSS radius matches the real window's", () => {
  const css = readFileSync("src/style.css", "utf8");
  const conf = JSON.parse(readFileSync("src-tauri/tauri.conf.json", "utf8"));
  const windowRadius = conf.app.windows[0].windowEffects.radius;
  expect(css).toMatch(new RegExp(`--win-radius:\\s*${windowRadius}px`));             // CSS and native window must agree
  expect(css).toMatch(/\.iconbtn\.corner\s*\{[^}]*border-radius:\s*calc\(var\(--win-radius\)\s*-\s*var\(--edge-gap\)\)/);   // outer radius minus the gap
  expect(css).toMatch(/\.iconbtn\.corner\s*\{[^}]*margin-top:\s*var\(--edge-gap\)/);
});


// ---------------- Add song: every song from the session, and why they wait ----------------
async function paste(url: string) {
  ($("#url") as HTMLInputElement).value = url;
  ($("#url") as HTMLInputElement).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await tick(80);
}

test("Add song keeps every song added this session, in the order added (a playlist)", async () => {
  await boot();
  await paste("https://soundcloud.com/x/one");
  await paste("https://soundcloud.com/x/two");
  await paste("https://soundcloud.com/x/three");
  const titles = $$("#latest .card").map((c) => c.querySelector(".title")!.textContent);
  expect(titles).toHaveLength(3);
  expect(titles[1]).toBe("Second Song");
  expect($$("#latest .card .status").length).toBe(3);                                  // each has its own state
});

test("a song that finishes stays in the list as a finished card while the others carry on", async () => {
  await boot();
  await paste("https://soundcloud.com/x/free");
  await paste("https://soundcloud.com/x/two");
  const [first] = W.jobs.filter((j) => j.track_key === "t2");
  first.status = "done";
  await tick(1500);
  const cards = $$("#latest .card");
  expect(cards).toHaveLength(2);
  expect(cards[0].querySelectorAll(".chip")).toHaveLength(2);                          // the first is now a finished song with bpm and key
  expect(cards[1].querySelector(".status")!.textContent).toContain("Splitting stems");
});

test("pasting the same song twice does not make two cards", async () => {
  await boot();
  await paste("https://soundcloud.com/x/two?si=abc");
  await paste("https://soundcloud.com/x/two");
  expect($$("#latest .card")).toHaveLength(1);
});

const ABLETON_PAUSED = "Track analysis is paused whilst Ableton is open, you can resume the analysis using the toggle";

test("while Ableton is open, queued songs say Paused and the line under the input says why, with the toggle", async () => {
  W.state = "paused_ableton";
  await boot();
  await paste("https://soundcloud.com/x/one");
  await paste("https://soundcloud.com/x/two");
  expect($$("#latest .card .status").map((s) => s.textContent)).toEqual(["Paused", "Paused"]);
  expect($("#qcontrol").hidden).toBe(false);
  expect($("#qcontrol [data-qtext]").textContent).toBe(ABLETON_PAUSED);
  expect([...$("#qcontrol [data-qtoggle]").querySelectorAll("button")].map((b) => b.textContent)).toEqual(["▶ Play", "❚❚ Pause"]);
  expect($("#qcontrol [data-act='pause']").classList.contains("active")).toBe(true);          // it is paused, so Pause is the lit side
  expect(($("#qcontrol [data-qtoggle]") as HTMLElement).style.getPropertyValue("--i")).toBe("1");
});

test("the Play / Pause toggle sits under the paste bar, and the old pause button in the corner is gone", async () => {
  W.state = "paused_ableton";
  await boot();
  await paste("https://soundcloud.com/x/one");
  const pane = $("#pane-add");
  const order = [...pane.children].map((c) => c.id);
  expect(order.indexOf("add")).toBeLessThan(order.indexOf("qcontrol"));
  expect(order.indexOf("qcontrol")).toBeLessThan(order.indexOf("latest"));
  expect(document.querySelector("#pause")).toBeNull();
  expect($$(".titlebar button").map((b) => b.id)).toEqual(["keybtn", "close"]);
});

test("pressing Play runs the queue even though Ableton is open", async () => {
  W.state = "paused_ableton";
  await boot();
  await paste("https://soundcloud.com/x/one");
  click($("#qcontrol [data-act='resume']"));
  await tick(1500);
  expect(W.calls.some((c) => c.path === "/resume" && c.method === "POST")).toBe(true);
  expect($("#latest .card .status").textContent).toBe("Splitting stems…");
  expect($("#qcontrol [data-qtext]").textContent).toBe("Track analysis is running, you can pause it using the toggle");
  expect($("#qcontrol [data-act='resume']").classList.contains("active")).toBe(true);
});

test("pressing Pause holds everything, and the songs say Paused", async () => {
  await boot();
  await paste("https://soundcloud.com/x/one");
  expect($("#qcontrol [data-qtext]").textContent).toContain("running");
  click($("#qcontrol [data-act='pause']"));
  await tick(1500);
  expect(W.calls.some((c) => c.path === "/pause")).toBe(true);
  expect($("#latest .card .status").textContent).toBe("Paused");
  expect($("#qcontrol [data-qtext]").textContent).toBe("Track analysis is paused, you can resume the analysis using the toggle");
});

test("clicking the side that is already lit does nothing", async () => {
  W.state = "paused_ableton";
  await boot();
  await paste("https://soundcloud.com/x/one");
  click($("#qcontrol [data-act='pause']"));                                                   // already paused
  await tick(80);
  expect(W.calls.filter((c) => c.path === "/pause" || c.path === "/resume")).toHaveLength(0);
});

test("the toggle only shows when there is work or a pause to resume from", async () => {
  await boot();
  expect($("#qcontrol").hidden).toBe(true);                                                    // nothing queued, nothing paused
  W.state = "paused_user";
  await tick(1500);
  expect($("#qcontrol").hidden).toBe(false);                                                   // paused: there must be a way to resume
});

test("songs still waiting from an earlier session are shown, so queued work never vanishes after a restart", async () => {
  W.state = "paused_ableton";
  W.jobs = [
    { id: 5, source: "https://soundcloud.com/x/like-that", status: "pending", stage: "", error: null, title: "Like That (Feat. DJ Dre)", artist: "L.P. Rhythm", track_key: "k:like-that" },
    { id: 4, source: "https://soundcloud.com/x/only-you", status: "pending", stage: "", error: null, title: "ONLY YOU (UNRELEASED)", artist: "MAX DEAN", track_key: "k:only-you" },
    { id: 3, source: "https://soundcloud.com/x/free", status: "done", stage: "", error: null, title: "Free", artist: "Joss Dean", track_key: "t2" }];
  await boot();                                                                               // nothing was added in THIS session
  expect($$("#latest .card").map((c) => c.querySelector(".title")!.textContent)).toEqual(["ONLY YOU (UNRELEASED)", "Like That (Feat. DJ Dre)"]);   // oldest first; the finished one lives in Collections
  expect($$("#latest .card .status").map((c) => c.textContent)).toEqual(["Paused", "Paused"]);
});

test("a finished song from an earlier session is not in Add song (it is in Collections)", async () => {
  W.jobs = [{ id: 3, source: "https://soundcloud.com/x/free", status: "done", stage: "", error: null, title: "Free", artist: "Joss Dean", track_key: "t2" }];
  await boot();
  expect($$("#latest .card")).toHaveLength(0);
});

test("a deliberate pause reads Paused too", async () => {
  W.state = "paused_user";
  await boot();
  await paste("https://soundcloud.com/x/one");
  expect($("#latest .card .status").textContent).toBe("Paused");
  expect($("#qcontrol [data-qtext]").textContent).toBe("Track analysis is paused, you can resume the analysis using the toggle");
});

test("a song mid-analysis when you pause also reads Paused", async () => {
  W.state = "paused_user";
  W.jobs = [{ id: 5, source: "https://soundcloud.com/x/one", status: "running", stage: "embed", error: null, title: "One", artist: "A", track_key: "k:one" }];
  await boot();
  await paste("https://soundcloud.com/x/one");
  expect($("#latest .card .status").textContent).toBe("Paused");
});

test("a song queued behind a running one says it is up next", async () => {
  W.state = "working";
  W.jobs = [{ id: 4, source: "https://soundcloud.com/x/other", status: "running", stage: "stems", error: null, title: "Other", artist: "A", track_key: "k:other" }];
  await boot();
  await paste("https://soundcloud.com/x/two");
  const statuses = $$("#latest .card .status").map((s) => s.textContent);
  expect(statuses).toEqual(["Splitting stems…", "Up next"]);
});


// ---------------- collections: scrolling, the (i), and staying on screen ----------------
test("an open collection's songs sit in their own scrollable list", async () => {
  W.collections = [{ id: 1, name: "UK minimal", track_ids: ["t1", "t2"] }];
  await boot();
  tab("collections");
  const list = $("#folders .folder .folder-list");
  expect(list.querySelectorAll(".mini")).toHaveLength(2);
  expect(list.parentElement!.classList.contains("folder-inner")).toBe(true);         // inside the element that animates open/closed
  const css = readFileSync("src/style.css", "utf8");
  expect(css).toMatch(/\.folder-list\s*\{[^}]*max-height:\s*\d+px[^}]*overflow-y:\s*auto/);   // taller than that: the wheel scrolls the songs
  expect(css).toMatch(/\.folder-inner\s*\{[^}]*overflow:\s*hidden/);                              // and the open/close animation still works
  expect(css).not.toMatch(/\.folder-list\s*\{[^}]*overscroll-behavior:\s*contain/);               // at the ends the wheel carries on to the page
});

test("the (i) sits left of New collection and its tooltip says how to share and remove tracks", async () => {
  await boot();
  tab("collections");
  const bar = $("#pane-collections .coll-bar");
  expect([...bar.children].map((c) => c.className.split(" ")[0])).toEqual(["info", "btn"]);   // (i) on the left, button on the right
  expect(bar.querySelector(".info b")!.textContent).toBe("i");
  expect(bar.querySelector(".infotip")!.textContent).toBe(
    "You can have the same track in multiple collections. To do this you must first create the new collection and then drag the track from the one collection into the other. To remove tracks from collections you must click the 'x' icon which appears when your mouse is hovering over that track.");
  expect(bar.querySelector(".infotip")!.getAttribute("role")).toBe("tooltip");
  const css = readFileSync("src/style.css", "utf8");
  expect(css).toMatch(/\.info:hover \.infotip[^{]*\{[^}]*opacity:\s*1/);                          // appears on hover
});

test("the tooltip's claims are true: dragging into a second folder keeps it in the first, and the x removes it", async () => {
  W.collections = [{ id: 1, name: "A", track_ids: ["t1"] }, { id: 2, name: "B", track_ids: [] }];
  await boot();
  tab("collections");
  const drop = new Event("drop", { bubbles: true, cancelable: true }) as any;
  drop.dataTransfer = { getData: () => "t1" };
  $$("#folders .folder")[1].dispatchEvent(drop);
  await tick(80);
  expect(W.collections.map((c) => c.track_ids)).toEqual([["t1"], ["t1"]]);             // same track in both
  expect($$("#folders .folder")[0].querySelector(".mini [data-remove]")).toBeTruthy();   // the x that removes it
});

test("a tall window near the bottom of the screen slides up so its lower half stays reachable", async () => {
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return (this as HTMLElement).id === "content" ? 900 : 30; } });
  screen.y = 700; screen.height = 1000;
  await boot();
  FakeResizeObserver.all.forEach((o) => o.cb());
  await tick(80);
  expect(setPosition).toHaveBeenCalled();
  const moved = setPosition.mock.calls.at(-1)![0];
  expect(moved.y).toBe(1000 - 8 - 660);                                                  // bottom edge kept 8px above the screen's edge
  expect(moved.x).toBe(50);                                                              // sideways position untouched
});

test("a window that already fits is not moved", async () => {
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return (this as HTMLElement).id === "content" ? 900 : 30; } });
  screen.y = 100; screen.height = 1000;
  await boot();
  FakeResizeObserver.all.forEach((o) => o.cb());
  await tick(80);
  expect(setPosition).not.toHaveBeenCalled();
});
