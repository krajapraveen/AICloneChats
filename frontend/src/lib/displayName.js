/**
 * Produce a human-friendly display label for a user across the app.
 *
 * Rules (in priority order):
 *   1. If `user.name` is a non-empty string → use it as-is.
 *   2. If the email is Apple's private relay (`*@privaterelay.appleid.com`)
 *      → render "Apple user" (the relay address is essentially a UUID;
 *      showing it adds zero signal to the user and looks broken).
 *   3. Otherwise → show the email.
 *
 * This is intentionally synchronous and pure so it can be called inline
 * from JSX without memoisation overhead.
 */
export function displayNameOf(user) {
  if (!user) return "";
  const name = typeof user.name === "string" ? user.name.trim() : "";
  if (name) return name;
  const email = (user.email || "").toLowerCase();
  if (email.endsWith("@privaterelay.appleid.com")) return "Apple user";
  return user.email || "";
}
