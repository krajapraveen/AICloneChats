/**
 * Canonical host enforcement.
 *
 * If the user lands on www.aiclonechats.com, redirect them to https://aiclonechats.com
 * BEFORE any auth code runs. Reasons:
 *   1. Google OAuth origin is registered for the apex; popup flow must match.
 *   2. localStorage is per-origin — sessions don't carry across www/apex.
 *   3. Avoids cookie / state inconsistency between subdomains.
 *
 * This is a tiny, eager redirect: runs at module load time so it happens before
 * React mounts, before AuthProvider checks /me, and before any popup is opened.
 *
 * Only redirects on production-like host. Preview/local hosts are left alone.
 */
export function enforceCanonicalHost() {
  if (typeof window === "undefined") return;
  const host = window.location.host;
  if (host === "www.aiclonechats.com") {
    const target = `https://aiclonechats.com${window.location.pathname}${window.location.search}${window.location.hash}`;
    // Use replace so the back button doesn't bounce the user back to www.
    window.location.replace(target);
  }
}
