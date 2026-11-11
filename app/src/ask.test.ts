import { expect, test } from "vitest";
import { formatAnswer } from "./ask";

test("bold, bullets, escaping", () => {
  expect(formatAnswer("a **b** c")).toBe("a <strong>b</strong> c");
  expect(formatAnswer("**one** and **two**")).toBe("<strong>one</strong> and <strong>two</strong>");
  expect(formatAnswer("* x\n- y\n  * z")).toBe("• x\n• y\n• z");
  expect(formatAnswer("1. **Keep it simple:** use one oscillator")).toBe("1. <strong>Keep it simple:</strong> use one oscillator");
  expect(formatAnswer("<b>no</b> & \"quotes\"")).toBe("&lt;b&gt;no&lt;/b&gt; &amp; &quot;quotes&quot;");
});

test("things that look like markdown but aren't are left alone", () => {
  expect(formatAnswer("**unfinished")).toBe("**unfinished");
  expect(formatAnswer("2 * 3 = 6")).toBe("2 * 3 = 6");                       // a lone asterisk mid-line is maths, not a bullet
  expect(formatAnswer("-3.54 dB at 250-500 Hz")).toBe("-3.54 dB at 250-500 Hz"); // negative numbers are not bullets
});
