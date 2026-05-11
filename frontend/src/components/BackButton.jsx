/**
 * Global back-navigation control.
 *
 * Placement: top-left, subtle, sticky-enough-to-find but never crowding
 * the page header. Mounted by App.js at the root so it appears on every
 * route. Suppressed on a small list of "entry surfaces" (landing, auth,
 * dashboard, admin index, public clone pages, the email-reveal page)
 * where a Back arrow is either redundant or visually noisy.
 *
 * Behavior:
 *   - If browser history has an entry to go back to → history.back()
 *   - Otherwise → fallback route:
 *       authenticated user → /dashboard
 *       unauthenticated    → /
 *
 * Safe-area-aware so it doesn't collide with iOS notch / Android cutouts.
 */
import { useLocation, useNavigate, matchPath } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

// Routes where the back button is intentionally hidden. These are the
// "you arrived, you didn't navigate here from somewhere" surfaces.
const SUPPRESS_PATTERNS = [
  "/",
  "/login",
  "/register",
  "/dashboard",
  "/admin",
  "/auth/callback",
  "/open/:token",
  "/v/:shareId",
];

function shouldSuppress(pathname) {
  return SUPPRESS_PATTERNS.some((pattern) => matchPath({ path: pattern, end: true }, pathname));
}

export default function BackButton() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();

  if (shouldSuppress(location.pathname)) return null;

  const onClick = () => {
    // window.history.length is always ≥ 1 (the current entry). A value
    // of 1 means this tab opened directly on this route — no in-app
    // history to walk back through. Fall back to a sensible root.
    if (typeof window !== "undefined" && window.history.length > 1) {
      navigate(-1);
      return;
    }
    navigate(user ? "/dashboard" : "/", { replace: true });
  };

  return (
    <div
      className="fixed z-30 pointer-events-none"
      style={{
        top: "calc(64px + env(safe-area-inset-top, 0px) + 8px)",
        left: "calc(env(safe-area-inset-left, 0px) + 12px)",
      }}
      data-testid="back-button-wrapper"
    >
      <button
        type="button"
        onClick={onClick}
        className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-bg/80 backdrop-blur-md px-3 py-1.5 text-[11px] font-mono uppercase tracking-widest text-ink/75 hover:text-ink hover:border-white/25 transition shadow-sm"
        aria-label="Go back"
        data-testid="back-button"
      >
        <span aria-hidden="true" className="text-sm leading-none">←</span>
        <span>Back</span>
      </button>
    </div>
  );
}
