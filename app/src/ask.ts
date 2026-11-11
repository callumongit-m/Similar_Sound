// "Ask me about it": one box used for a song and for a comparison.
// If no API key is connected yet it says so and offers to open the key panel (the key icon, top left).
// Running out of credits shows the plain sentence.
import { ApiError } from "./api";
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
  const show = (html: string, cls = "") => { out.innerHTML = `<div class="answer ${cls} rise">${html}</div>`; };

  async function ask(question: string) {
    show("Thinking…", "thinking");
    try {
      show(esc((await send(question)).answer));
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_key") {
        show(`${esc(e.message)} <button class="btn small" data-openkey style="margin-left:6px">Add key</button>`, "notice");
      } else show(esc(errorText(e)), e instanceof ApiError && e.code === "credits" ? "notice" : "err");
    }
  }

  const submit = () => {
    const field = $<HTMLTextAreaElement>("[data-q]", box);
    const q = field.value.trim();
    if (!q) return;
    field.value = "";                       // sent: ready for the next question
    ask(q);
  };

  box.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("[data-ask]")) submit();
    else if (t.closest("[data-openkey]")) document.dispatchEvent(new CustomEvent("open-key-panel"));
  });
  // Enter sends, like a chat. Shift+Enter makes a new line. (isComposing: Enter confirms an IME character.)
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && (e.target as HTMLElement).matches("[data-q]")) {
      e.preventDefault();
      submit();
    }
  });
}
