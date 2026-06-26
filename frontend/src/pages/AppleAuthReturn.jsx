/**
 * /auth/apple/return — final landing point of the Sign in with Apple flow.
 *
 * Apple's web OAuth uses `response_mode=form_post`, so Apple POSTs back to our
 * BACKEND callback (not the frontend). The backend creates the user/session
 * and then 302s the browser to this route with the session token in the URL
 * FRAGMENT:
 *
 *   /auth/apple/return#token=<session_token>&next=/dashboard
 *
 * Why the fragment and not a query string?
 *   - Fragments are NEVER sent to the server (no access-log leak)
 *   - Browser referrer policies usually drop fragments on outgoing links too
 *   - Token still flows server → SPA in a single hop
 *
 * What we do here:
 *   1. Read `token` + `next` from `window.location.hash`
 *   2. Persist `token` to localStorage (where /lib/api.js reads it as Bearer)
 *   3. Wipe the fragment from history so the token doesn't sit in the URL bar
 *   4. Refresh the AuthContext (full reload — cleanest way to hydrate /api/me)
 *   5. Navigate to `next` (or /dashboard)
 *
 * Edge case: if the backend redirected here with `error=…` in the fragment
 * instead of a token, we surface it as a toast and bounce to /login.
 */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

export default function AppleAuthReturn() {
  const navigate = useNavigate();

  useEffect(() => {
    const hash = (window.location.hash || "").replace(/^#/, "");
    const params = new URLSearchParams(hash);
    const token = params.get("token");
    const next = params.get("next") || "/dashboard";
    const error = params.get("error");

    if (error) {
      toast.error(`Apple sign-in failed: ${error}`);
      navigate("/login", { replace: true });
      return;
    }

    if (!token) {
      toast.error("Apple sign-in returned no session. Please try again.");
      navigate("/login", { replace: true });
      return;
    }

    // Persist the session BEFORE wiping the fragment so we never have a moment
    // where the URL has been cleared but the token wasn't stored.
    try {
      localStorage.setItem("session_token", token);
    } catch {
      // localStorage may throw in incognito with quotas etc. The cookie set
      // by the backend will still work for SSR/cookie-based paths, but the
      // Bearer header path is broken — surface that as an error.
      toast.error("Couldn't save your sign-in locally. Try a non-incognito window?");
      navigate("/login", { replace: true });
      return;
    }

    // Strip the fragment from the URL bar before navigating away so the
    // token doesn't sit in browser history.
    try {
      window.history.replaceState(null, "", "/auth/apple/return");
    } catch {
      // ignore
    }

    // Full reload to hydrate AuthContext cleanly with the new token.
    // Using window.location instead of navigate() because AuthContext is
    // initialised once at mount; a replace() inside React wouldn't re-trigger
    // the /api/me bootstrap.
    const safeNext = next.startsWith("/") ? next : "/dashboard";
    window.location.replace(safeNext);
  }, [navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-stone-950 text-ink"
         data-testid="apple-auth-return">
      <div className="text-center space-y-3">
        <div className="text-violet-300 font-mono text-xs uppercase tracking-widest">
          Signing you in
        </div>
        <div className="text-2xl font-display">Welcome 🍎</div>
        <div className="text-sm text-muted">Hang tight — taking you to your dashboard…</div>
      </div>
    </div>
  );
}
