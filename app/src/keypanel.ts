// The key icon's sheet: paste an API key from any company. The service works out whose it is, proves it
// works with a free call, picks the cheapest model and stores it in the Keychain. The key is cleared from
// this page the moment it is saved, and only the company and model are ever shown back.
import { ApiError, api, type LlmSummary, type Provider } from "./api";
import { S } from "./state";
import { $, errorText, esc } from "./ui";

export const keyPanelHtml = `
  <div class="keypanel" id="keypanel"><div class="kp-inner"><div class="kp-body">
    <div class="kp-status" data-status></div>
    <div class="row"><input class="field" data-key type="password" placeholder="Paste an API key (Gemini, OpenAI, Claude, ...)" autocomplete="off" spellcheck="false" /><button class="btn small" data-save>Save</button></div>
    <div class="kp-more" data-more hidden></div>
    <div class="kp-msg" data-msg></div>
    <div class="kp-foot">Saved in your Mac's Keychain. Only analysis numbers are sent, never audio. <button class="linkbtn" data-remove hidden>Remove key</button></div>
  </div></div></div>`;

let el: HTMLElement;
let onChange: () => void = () => {};

const q = <T extends HTMLElement>(sel: string) => $<T>(sel, el);

function describe(l: LlmSummary | null) {
  q("[data-status]").innerHTML = l?.configured ? `Connected to <b>${esc(l.name)}</b> · ${esc(l.model)}` : "No key yet. Add one to ask questions about your songs.";
  q("[data-remove]").hidden = !l?.configured;
}

/** Shown only when the server can't tell whose key it is, or needs one more detail. */
function more(error: ApiError) {
  const m = q("[data-more]");
  const providers: Provider[] = error.extra?.providers ?? [];
  const wantsCompany = error.code === "unknown_provider";
  const wantsAddress = error.code === "need_address";
  const wantsModel = error.code === "need_model";
  m.hidden = false;
  const keep = (sel: string) => (m.querySelector<HTMLInputElement>(sel)?.value ?? "");
  const [prov, addr, mod] = [keep("[data-provider]"), keep("[data-address]"), keep("[data-model]")];
  m.innerHTML = `
    <label>Which company is this key from?
      <select class="field" data-provider>${providers.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select></label>
    <label data-address-row hidden>Address (starts with https://)<input class="field" data-address placeholder="https://api.example.com/v1" spellcheck="false" /></label>
    <label data-model-row hidden>Model name<input class="field" data-model placeholder="e.g. a small, cheap model" spellcheck="false" /></label>`;
  const select = m.querySelector<HTMLSelectElement>("[data-provider]")!;
  if (prov) select.value = prov;
  const sync = () => {
    const custom = select.value === "custom";
    m.querySelector<HTMLElement>("[data-address-row]")!.hidden = !(custom || wantsAddress);
    m.querySelector<HTMLElement>("[data-model-row]")!.hidden = !(custom || wantsModel);
  };
  select.onchange = sync; sync();
  if (addr) m.querySelector<HTMLInputElement>("[data-address]")!.value = addr;
  if (mod) m.querySelector<HTMLInputElement>("[data-model]")!.value = mod;
  void wantsCompany;
}

async function save() {
  const input = q<HTMLInputElement>("[data-key]");
  const key = input.value.trim();
  if (!key) return;
  const msg = q("[data-msg]");
  const sel = q("[data-more]").querySelector<HTMLSelectElement>("[data-provider]");
  const body: Parameters<typeof api.saveKey>[0] = { key };
  if (sel && !q("[data-more]").hidden) {
    body.provider = sel.value;
    const a = q("[data-more]").querySelector<HTMLInputElement>("[data-address]")?.value.trim();
    const m = q("[data-more]").querySelector<HTMLInputElement>("[data-model]")?.value.trim();
    if (a) body.base_url = a;
    if (m) body.model = m;
  }
  msg.className = "kp-msg"; msg.textContent = "Checking…";
  try {
    S.llm = await api.saveKey(body);
    input.value = "";                                   // gone from the page the moment it is saved
    q("[data-more]").hidden = true;
    msg.className = "kp-msg ok"; msg.textContent = "Connected.";
    describe(S.llm); onChange();
    setTimeout(() => close(), 1100);
  } catch (e) {
    msg.className = "kp-msg err"; msg.textContent = errorText(e);
    if (e instanceof ApiError && ["unknown_provider", "need_address", "need_model"].includes(e.code)) more(e);
  }
}

export function openKeyPanel() {
  el.classList.add("open");
  describe(S.llm);
  q("[data-msg]").textContent = "";
  setTimeout(() => q<HTMLInputElement>("[data-key]").focus(), 250);
}
export function close() { el.classList.remove("open"); }
export const toggleKeyPanel = () => (el.classList.contains("open") ? close() : openKeyPanel());

export function mountKeyPanel(change: () => void) {
  el = $("#keypanel");
  onChange = change;
  el.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("[data-save]")) save();
    else if (t.closest("[data-remove]")) api.removeKey().then((l) => { S.llm = l; describe(l); onChange(); }).catch((x) => { q("[data-msg]").textContent = errorText(x); });
  });
  el.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.target as HTMLElement).matches("[data-key]")) save(); if (e.key === "Escape") close(); });
  document.addEventListener("open-key-panel", openKeyPanel);
}
