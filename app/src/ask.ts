// "Ask me about it": one box used for a song and for a comparison.
// If Gemini has no key yet the box asks for it right there; the key goes to the service once (it lands in
// the macOS Keychain) and is never shown again. Running out of credits shows the plain sentence.
import { ApiError, api } from "./api";
import { $, errorText, esc } from "./ui";

export const askBoxHtml = (placeholder: string) => `
  <div class="askbox" data-askbox>
    <textarea data-q placeholder="${esc(placeholder)}"></textarea>
    <div class="ask-actions"><button class="btn small" data-ask>Ask</button></div>
    <div data-answer></div>
  </div>`;

export function wireAsk(root: HTMLElement, send: (question: string) => Promise<{ answer: string }>) {
  const box = $<HTMLElement>("[data-askbox]", root);
  if (!box) return;
  const out = $<HTMLElement>("[data-answer]", box);
  let lastQuestion = "";

  const show = (html: string, cls = "") => { out.innerHTML = `<div class="answer ${cls} rise">${html}</div>`; };

  async function ask(question: string) {
    lastQuestion = question;
    show("Thinking…", "thinking");
    try {
      show(esc((await send(question)).answer));
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_key") keyForm(e.message);
      else show(esc(errorText(e)), e instanceof ApiError && e.code === "credits" ? "notice" : "err");
    }
  }

  function keyForm(message: string) {
    out.innerHTML = `<div class="answer keyform rise"><div>${esc(message)} Paste it here once; it's saved in your Mac's Keychain, never in a file.</div>
      <div class="row" style="margin-top:8px"><input class="field" data-key type="password" placeholder="Gemini API key" autocomplete="off" spellcheck="false" />
      <button class="btn small" data-savekey>Save</button></div><div class="sub wrap err" data-keyerr></div></div>`;
  }

  box.addEventListener("click", async (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("[data-ask]")) {
      const q = $<HTMLTextAreaElement>("[data-q]", box).value.trim();
      if (q) ask(q);
    } else if (t.closest("[data-savekey]")) {
      const input = $<HTMLInputElement>("[data-key]", box);
      const err = $<HTMLElement>("[data-keyerr]", box);
      const key = input.value.trim();
      if (!key) return;
      err.textContent = "";
      try {
        await api.saveKey(key);
        input.value = "";                       // gone from the page the moment it is saved
        if (lastQuestion) ask(lastQuestion);
      } catch (ex) {
        err.textContent = errorText(ex);
      }
    }
  });
  box.addEventListener("keydown", (e) => {
    const t = e.target as HTMLElement;
    if (e.key === "Enter" && t.matches("[data-key]")) $<HTMLElement>("[data-savekey]", box).click();
  });
}
