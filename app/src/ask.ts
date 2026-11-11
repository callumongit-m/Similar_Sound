// "Ask me about it": one box used for a song and for a comparison.
// If no API key is connected yet it says so and offers to open the key panel (the key icon, top left).
// Running out of credits shows the plain sentence.
import { ApiError, type Turn } from "./api";
import { $, errorText, esc } from "./ui";

/** Turns a model's reply into safe HTML: **bold**, "- " / "* " bullets as dots, "#" headings as bold lines and a
 *  line of *** or --- as a divider; line breaks and numbered lines are kept as written. Everything is escaped
 *  first, so a reply can never inject markup. */
export function formatAnswer(text: string): string {
  return esc(text)
    .replace(/^[ \t]*([*_-])(?:[ \t]*\1){2,}[ \t]*$/gm, "<hr>")
    .replace(/^[ \t]*#{1,6}[ \t]+(.+)$/gm, "<strong class=\"ah\">$1</strong>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^[ \t]*[*-][ \t]+/gm, "• ");
}

/** The "Ask about ..." heading, the one-line caveat about what data exists, and the conversation box. */
export const askSection = (title: string, placeholder: string, extraNote = "") => `
  <div class="section">${esc(title)}</div>
  <div class="section-sub">Each song only has data for the main build up and drop.</div>
  ${extraNote ? `<div class="section-sub">${esc(extraNote)}</div>` : ""}
  ${askBoxHtml(placeholder)}`;

export const askBoxHtml = (placeholder: string) => `
  <div class="askbox" data-askbox>
    <div class="thread" data-thread data-answer></div>
    <textarea data-q placeholder="${esc(placeholder)}"></textarea>
    <div class="ask-actions"><button class="btn small" data-ask>Ask</button><button class="linkbtn" data-newchat hidden>New conversation</button></div>
  </div>`;

/** A conversation: each question is sent with the earlier ones (the service keeps nothing), so follow-ups work. */
export function wireAsk(root: HTMLElement, send: (question: string, history: Turn[]) => Promise<{ answer: string }>) {
  const box = $<HTMLElement>("[data-askbox]", root);
  if (!box) return;
  const thread = $<HTMLElement>("[data-thread]", box);
  const newChat = $<HTMLElement>("[data-newchat]", box);
  const turns: Turn[] = [];                 // only answered questions: a failed one is not part of the conversation
  let busy = false;

  async function ask(question: string) {
    busy = true;
    const turn = document.createElement("div");
    turn.className = "turn";
    turn.innerHTML = `<div class="q">${esc(question)}</div><div class="answer thinking rise">Thinking…</div>`;
    thread.appendChild(turn);
    turn.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    const out = $<HTMLElement>(".answer", turn);
    const show = (html: string, cls = "") => { out.className = `answer ${cls} rise`.trim(); out.innerHTML = html; };
    try {
      const { answer } = await send(question, turns.slice());
      turns.push({ q: question, a: answer });
      show(formatAnswer(answer));
      newChat.hidden = false;
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_key") {
        show(`${esc(e.message)} <button class="btn small" data-openkey style="margin-left:6px">Add key</button>`, "notice");
      } else show(esc(errorText(e)), e instanceof ApiError && e.code === "credits" ? "notice" : "err");
    } finally {
      busy = false;
    }
  }

  const submit = () => {
    const field = $<HTMLTextAreaElement>("[data-q]", box);
    const q = field.value.trim();
    if (!q || busy) return;
    field.value = "";                       // sent: ready for the next question
    ask(q);
  };

  box.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("[data-ask]")) submit();
    else if (t.closest("[data-openkey]")) document.dispatchEvent(new CustomEvent("open-key-panel"));
    else if (t.closest("[data-newchat]")) { turns.length = 0; thread.innerHTML = ""; newChat.hidden = true; }
  });
  // Enter sends, like a chat. Shift+Enter makes a new line. (isComposing: Enter confirms an IME character.)
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && (e.target as HTMLElement).matches("[data-q]")) {
      e.preventDefault();
      submit();
    }
  });
}
