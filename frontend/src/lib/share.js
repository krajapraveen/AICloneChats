/**
 * Native share with clipboard fallback.
 *
 * Spec (2026-05-11):
 *   - If `navigator.share` is available, open the native share sheet.
 *   - If the user cancels the share sheet, treat as a no-op (no error toast).
 *   - If `navigator.share` is unavailable OR throws a non-cancel error, copy
 *     the text to the clipboard and tell the user where to paste it.
 *   - If both share and copy fail, surface a "Could not share. Please copy
 *     manually." toast — never silently swallow the failure.
 *
 * Returns:
 *   { ok: boolean, method: "native" | "clipboard_fallback" | null,
 *     cancelled: boolean }
 *
 * `method === null` means everything failed; the caller is responsible for
 * showing the visible "Could not share" toast (callers usually already do
 * via the `ok===false && !cancelled` branch).
 */
import { copyToClipboard } from "./clipboard";

// AbortError is what browsers throw when the user dismisses the native sheet.
const USER_CANCEL_ERROR_NAMES = new Set(["AbortError"]);

export async function shareText({ text, title, url } = {}) {
  const payload = text && text.trim();
  if (!payload) {
    return { ok: false, method: null, cancelled: false, reason: "empty" };
  }

  // 1) Native share sheet — preferred on mobile
  if (typeof navigator !== "undefined" && typeof navigator.share === "function") {
    try {
      const data = { text: payload };
      if (title) data.title = title;
      if (url) data.url = url;
      await navigator.share(data);
      return { ok: true, method: "native", cancelled: false };
    } catch (err) {
      // User cancelled the share sheet — DO NOT show an error toast.
      if (err && USER_CANCEL_ERROR_NAMES.has(err.name)) {
        return { ok: false, method: "native", cancelled: true };
      }
      // Fall through to clipboard fallback on any other failure (permission
      // denied, NotAllowedError on non-secure context, etc.)
    }
  }

  // 2) Clipboard fallback
  try {
    const copied = await copyToClipboard(payload);
    if (copied) {
      return { ok: true, method: "clipboard_fallback", cancelled: false };
    }
  } catch {
    // ignore; fall through to total-failure
  }
  return { ok: false, method: null, cancelled: false, reason: "share_and_copy_failed" };
}
