import { Link, NavLink, useNavigate, useLocation } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { useCredits } from "../hooks/useCredits";

/**
 * Public navbar — five anchors, no operator/admin tooling leak.
 *
 * - Active route indicator via NavLink (renders `aria-current="page"`).
 * - Mobile drawer: ESC closes, click-outside closes, first item auto-focused
 *   on open for keyboard users, body scroll-locked while open.
 * - All operator/observability surfaces live exclusively under /admin index
 *   when role === "admin".
 */
export default function Navbar() {
  const { user, logout } = useAuth();
  const credits = useCredits();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const drawerRef = useRef(null);
  const firstDrawerLinkRef = useRef(null);

  // Close drawer on route change (mobile users tapping a link).
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  // ESC closes drawer, click-outside closes drawer, scroll-lock while open.
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    const onPointer = (e) => {
      if (drawerRef.current && !drawerRef.current.contains(e.target)) {
        setMobileOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("touchstart", onPointer);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Focus first drawer link for keyboard users.
    requestAnimationFrame(() => {
      firstDrawerLinkRef.current?.focus();
    });
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("touchstart", onPointer);
      document.body.style.overflow = prevOverflow;
    };
  }, [mobileOpen]);

  const baseLink = "font-display font-bold text-sm transition";
  const inactive = "text-ink/80 hover:text-ink";
  const active = "text-ink";
  const adminInactive = "text-violet-soft hover:text-violet";
  const adminActive = "text-violet";

  return (
    <header className="border-b border-white/5 bg-bg/60 sticky top-0 z-40 backdrop-blur-xl safe-pt" data-testid="navbar">
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-3 sm:py-4 flex items-center justify-between gap-2">
        <Link to="/" className="flex items-center gap-2 group flex-shrink-0 min-w-0" data-testid="nav-logo">
          <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-base sm:text-lg shadow-glow-amber flex-shrink-0">
            C
          </div>
          <span className="font-display font-black text-lg sm:text-xl tracking-tight text-ink truncate">
            aiclonechats<span className="text-amber">.</span>com
          </span>
        </Link>

        {/* Desktop nav */}
        <nav className="hidden md:flex items-center gap-4 lg:gap-5 flex-shrink-0" data-testid="nav-desktop" aria-label="Primary">
          <NavLink to="/explore" className={({ isActive }) => `${baseLink} ${isActive ? active : inactive}`} data-testid="nav-explore">Explore</NavLink>
          {user && (
            <NavLink to="/dashboard" className={({ isActive }) => `${baseLink} ${isActive ? active : inactive}`} data-testid="nav-dashboard">Dashboard</NavLink>
          )}
          {user?.role === "admin" && (
            <NavLink to="/admin" className={({ isActive }) => `${baseLink} ${isActive ? adminActive : adminInactive}`} data-testid="nav-admin">Admin</NavLink>
          )}
          {user && !credits.loading && (
            credits.admin_unlimited ? (
              <Link to="/pricing" className="rounded-full border border-violet/40 bg-violet-500/10 px-3 py-1 text-[10px] font-mono uppercase tracking-widest text-violet-soft" data-testid="nav-credits-pill">∞ admin</Link>
            ) : (
              <Link to="/pricing" className="rounded-full border border-white/10 bg-bg/50 px-3 py-1 text-[11px] font-mono text-ink/85 hover:border-white/25 transition" data-testid="nav-credits-pill">
                <span className="text-amber">{credits.credits_balance ?? 0}</span> cr
              </Link>
            )
          )}
          {user ? (
            <div className="flex items-center gap-3">
              <span className="hidden lg:inline-block text-xs font-mono text-muted truncate max-w-[160px]" data-testid="nav-user-email">
                {user.email}
              </span>
              <button
                onClick={async () => { await logout(); navigate("/"); }}
                className="btn-ghost text-xs sm:text-sm"
                data-testid="nav-logout"
              >
                Log out
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Link to="/login" className="btn-ghost text-xs sm:text-sm" data-testid="nav-login">Log in</Link>
              <Link to="/register" className="btn-brutal text-xs sm:text-sm" data-testid="nav-signup">Get started</Link>
            </div>
          )}
        </nav>

        {/* Mobile hamburger */}
        <button
          className="md:hidden inline-flex items-center justify-center w-9 h-9 rounded-lg border border-white/10 text-ink/80 hover:text-ink hover:border-white/25 transition"
          onClick={() => setMobileOpen((v) => !v)}
          aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={mobileOpen}
          aria-controls="mobile-nav-drawer"
          data-testid="nav-mobile-toggle"
        >
          <span className="block w-4 leading-none text-base">{mobileOpen ? "✕" : "☰"}</span>
        </button>
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div
          id="mobile-nav-drawer"
          ref={drawerRef}
          className="md:hidden border-t border-white/5 bg-bg/95 backdrop-blur-xl"
          data-testid="nav-mobile-drawer"
          role="dialog"
          aria-modal="true"
          aria-label="Site navigation"
        >
          <div className="max-w-6xl mx-auto px-4 sm:px-5 py-3 flex flex-col gap-1">
            <NavLink
              ref={firstDrawerLinkRef}
              to="/explore"
              className={({ isActive }) => `py-2 text-sm font-display font-bold ${isActive ? "text-ink" : "text-ink/85"}`}
              data-testid="nav-mobile-explore"
            >Explore</NavLink>
            {user && (
              <NavLink to="/dashboard" className={({ isActive }) => `py-2 text-sm font-display font-bold ${isActive ? "text-ink" : "text-ink/85"}`} data-testid="nav-mobile-dashboard">Dashboard</NavLink>
            )}
            <NavLink to="/pricing" className={({ isActive }) => `py-2 text-sm font-display font-bold ${isActive ? "text-ink" : "text-ink/85"}`} data-testid="nav-mobile-pricing">Pricing</NavLink>
            {user?.role === "admin" && (
              <NavLink to="/admin" className={({ isActive }) => `py-2 text-sm font-display font-bold ${isActive ? "text-violet" : "text-violet-soft"}`} data-testid="nav-mobile-admin">Admin</NavLink>
            )}
            <div className="border-t border-white/5 mt-2 pt-2 flex items-center gap-2">
              {user ? (
                <>
                  <span className="text-[11px] font-mono text-muted truncate flex-1">{user.email}</span>
                  <button
                    onClick={async () => { setMobileOpen(false); await logout(); navigate("/"); }}
                    className="btn-ghost text-xs"
                    data-testid="nav-mobile-logout"
                  >
                    Log out
                  </button>
                </>
              ) : (
                <>
                  <Link to="/login" className="btn-ghost text-xs flex-1 text-center" data-testid="nav-mobile-login">Log in</Link>
                  <Link to="/register" className="btn-brutal text-xs flex-1 text-center" data-testid="nav-mobile-signup">Get started</Link>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
