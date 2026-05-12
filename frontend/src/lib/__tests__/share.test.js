/**
 * Regression: shareText helper contract.
 *
 * Bug history (2026-05-11):
 *   Voice → Message Share button was wired to open a "create public link"
 *   modal that was unreachable for the user's intent ("share the reply
 *   text"). User reported it as broken. Fix: Share calls navigator.share
 *   directly with the reply text, falls back to clipboard copy, and is a
 *   no-op silently on user cancel.
 *
 * This file LOCKS that contract with explicit cases:
 *   - navigator.share present + resolves → native success
 *   - navigator.share present + rejects with AbortError → cancelled (no error)
 *   - navigator.share present + rejects with other → falls back to clipboard
 *   - navigator.share absent → falls back to clipboard
 *   - empty text → no-op { ok: false, method: null }
 *   - clipboard also fails → { ok: false, method: null, reason: "share_and_copy_failed" }
 */

// Mock the clipboard module before importing shareText
jest.mock("../clipboard", () => ({
  copyToClipboard: jest.fn(),
}));

import { shareText } from "../share";
import { copyToClipboard } from "../clipboard";

const originalNavigator = global.navigator;

afterEach(() => {
  jest.resetAllMocks();
  // Restore navigator
  if (originalNavigator) {
    Object.defineProperty(global, "navigator", {
      value: originalNavigator,
      configurable: true,
      writable: true,
    });
  }
});

function setNavigator(nav) {
  Object.defineProperty(global, "navigator", {
    value: nav,
    configurable: true,
    writable: true,
  });
}

describe("shareText", () => {
  test("empty text returns { ok: false, method: null, reason: empty }", async () => {
    const result = await shareText({ text: "" });
    expect(result).toMatchObject({ ok: false, method: null, reason: "empty", cancelled: false });
  });

  test("whitespace-only text returns empty no-op", async () => {
    const result = await shareText({ text: "   \n  " });
    expect(result).toMatchObject({ ok: false, method: null, reason: "empty" });
  });

  test("uses navigator.share when available", async () => {
    const shareSpy = jest.fn().mockResolvedValue(undefined);
    setNavigator({ share: shareSpy });
    const result = await shareText({ text: "hi" });
    expect(shareSpy).toHaveBeenCalledTimes(1);
    expect(shareSpy).toHaveBeenCalledWith({ text: "hi" });
    expect(result).toEqual({ ok: true, method: "native", cancelled: false });
    expect(copyToClipboard).not.toHaveBeenCalled();
  });

  test("passes optional title and url through to navigator.share", async () => {
    const shareSpy = jest.fn().mockResolvedValue(undefined);
    setNavigator({ share: shareSpy });
    await shareText({ text: "body", title: "T", url: "https://x" });
    expect(shareSpy).toHaveBeenCalledWith({ text: "body", title: "T", url: "https://x" });
  });

  test("user cancel (AbortError) returns cancelled=true, NOT an error", async () => {
    const err = new Error("user cancelled");
    err.name = "AbortError";
    const shareSpy = jest.fn().mockRejectedValue(err);
    setNavigator({ share: shareSpy });
    const result = await shareText({ text: "hi" });
    expect(result).toEqual({ ok: false, method: "native", cancelled: true });
    expect(copyToClipboard).not.toHaveBeenCalled();
  });

  test("non-cancel share error falls back to clipboard copy", async () => {
    const err = new Error("permission denied");
    err.name = "NotAllowedError";
    const shareSpy = jest.fn().mockRejectedValue(err);
    setNavigator({ share: shareSpy });
    copyToClipboard.mockResolvedValue(true);
    const result = await shareText({ text: "hi" });
    expect(shareSpy).toHaveBeenCalled();
    expect(copyToClipboard).toHaveBeenCalledWith("hi");
    expect(result).toEqual({ ok: true, method: "clipboard_fallback", cancelled: false });
  });

  test("no navigator.share → falls back to clipboard", async () => {
    setNavigator({});
    copyToClipboard.mockResolvedValue(true);
    const result = await shareText({ text: "hi" });
    expect(copyToClipboard).toHaveBeenCalledWith("hi");
    expect(result).toEqual({ ok: true, method: "clipboard_fallback", cancelled: false });
  });

  test("both share absent AND clipboard fails → total failure", async () => {
    setNavigator({});
    copyToClipboard.mockResolvedValue(false);
    const result = await shareText({ text: "hi" });
    expect(result).toMatchObject({ ok: false, method: null, cancelled: false, reason: "share_and_copy_failed" });
  });

  test("clipboard throws → also total failure", async () => {
    setNavigator({});
    copyToClipboard.mockRejectedValue(new Error("boom"));
    const result = await shareText({ text: "hi" });
    expect(result.ok).toBe(false);
    expect(result.method).toBe(null);
  });
});
