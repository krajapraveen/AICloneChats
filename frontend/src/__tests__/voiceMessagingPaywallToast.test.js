/**
 * Regression guard: when the Voice Messaging surface gets a 402 from any
 * of its endpoints (transcribe, text-input, generate-all, generate,
 * refine), it must NOT show a redundant generic error toast on top of
 * the global paywall modal.
 *
 * This catches the "Could not generate messages. Try again." bug where
 * a 402 paywall trigger left an angry red toast under the modal.
 */
const fs = require("fs");
const path = require("path");

describe("VoiceMessaging 402 → no redundant toast", () => {
  const src = fs.readFileSync(
    path.join(__dirname, "../pages/VoiceMessaging.jsx"),
    "utf8"
  );

  test("handleLimitError swallows ANY 402, not just specific codes", () => {
    // The catch-all 402 branch must exist
    expect(src).toMatch(/if\s*\(\s*status\s*===\s*402\s*\)\s*{\s*return\s+true/);
  });

  test("every catch block that toasts a generic error guards it with handleLimitError", () => {
    // Find every `toast.error(...generate... | ...refine... | ...process audio...
    // | ...process text...` and confirm it's inside an `if (!handleLimitError(err))`
    // or after `if (handleLimitError(err)) return`.
    const toastLines = src.split("\n").map((l, i) => ({ line: i + 1, text: l }))
      .filter((r) => /toast\.error\(.+(?:generate|refine|process audio|process text|regenerate)/i.test(r.text));

    expect(toastLines.length).toBeGreaterThanOrEqual(5);
    for (const { line, text } of toastLines) {
      // Look back up to 6 lines for the guard
      const start = Math.max(0, line - 7);
      const window = src.split("\n").slice(start, line).join("\n");
      expect(window).toMatch(/handleLimitError\(err\)/);
    }
  });
});
