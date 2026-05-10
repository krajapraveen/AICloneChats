/**
 * Device ID for anonymous trial gating.
 * Persists in localStorage. Stable across sessions on the same browser.
 */
const KEY = "voice_device_id";

function rand() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
  return Math.random().toString(36).slice(2) + Date.now().toString(36) + Math.random().toString(36).slice(2);
}

export function getDeviceId() {
  if (typeof window === "undefined") return "ssr";
  let id = localStorage.getItem(KEY);
  if (!id) {
    id = `dev_${rand()}`;
    localStorage.setItem(KEY, id);
  }
  return id;
}
