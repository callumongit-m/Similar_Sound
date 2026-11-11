// Boots the real page against a fake service. Catches startup crashes (the window renders nothing) and
// checks the three parts behave: Add song, Collections, Compare my project.
import { afterEach, beforeEach, expect, test, vi } from "vitest";

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

const T1 = { id: "t1", title: "Temptation", artist: "obp", source: "https://soundcloud.com/obpofficial/temptation-5", bpm: 128, key: "C min" };
const T2 = { id: "t2", title: "Free", artist: "Joss Dean", source: "https://soundcloud.com/x/free", bpm: 126, key: "A min" };
const DOC = { ...T1, segments: { build: [120, 150], drop: [150, 180] }, alternative_drops: [], groove: { swing: 0.12 } };
const GAPS = { reference: "UK minimal", n: 1, is_collection: true, small: true, note: "UK minimal only has 1 song, so treat this as a rough guide.", references: ["Temptation"],
  gaps: [{ label: "Bass · sub level (dB)", yours: -9.1, reference: -5.2, delta: -3.9, outside: true, n: 3, text: "Bass · sub level (dB): yours -9.1; UK minimal averages -5.2, range -6.4 to -3.9 (below all 3)" },
         { label: "Groove · swing", yours: 0, reference: 0.12, delta: -0.12, outside: false, n: 3, text: "Groove · swing: yours 0.00; UK minimal averages 0.12, range 0.10 to 0.14" }] };

interface World { answerText?: string; llm: any; keyBodies: any[]; jobs: any[]; tracks: any[]; collections: any[]; listen: any; askError: any; calls: { method: string; path: string; body: any }[]; keySaved: boolean }
let W: World;
// The real service gives a job the same track_key from the moment it is created, so one card can follow it.
const keyFor = (url: string) => (url.endsWith("/free") ? "t2" : "k:" + url.split("/").pop()!.split("?")[0]);
const NO_KEY = { configured: false, provider: null, name: null, model: null };
const PROVIDERS = [{ id: "gemini", name: "Google Gemini", needs_address: false }, { id: "mistral", name: "Mistral", needs_address: false }, { id: "custom", name: "Other (OpenAI-compatible)", needs_address: true }];

function fakeService() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("http://127.0.0.1:8765", "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    W.calls.push({ method, path, body });
    const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
    const fail = (status: number, detail: unknown) => ({ ok: false, status, json: async () => ({ detail }) });
    if (path === "/status") return ok({ state: "idle", jobs: W.jobs });
    if (path === "/tracks") return ok(W.tracks);
    if (path === "/collections" && method === "GET") return ok(W.collections);
    if (path === "/collections" && method === "POST") { const c = { id: 9, name: body.name, track_ids: [] }; W.collections.push(c); return ok(c); }
    if (/^\/collections\/\d+\/tracks$/.test(path) && method === "POST") { W.collections.find((c) => c.id === Number(path.split("/")[2]))!.track_ids.push(body.track_id); return ok({}); }
    if (path === "/jobs" && method === "POST") { W.jobs.unshift({ id: 5, source: body.url, status: "running", stage: "stems", error: null, title: "Some Song", artist: "Someone", track_key: keyFor(body.url) }); return ok({ id: 5 }); }
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
afterEach(() => { timers.splice(0).forEach((id) => clearInterval(id)); });

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = '<div id="app"></div>';
  observed.length = 0;
  setSize.mockClear();
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
