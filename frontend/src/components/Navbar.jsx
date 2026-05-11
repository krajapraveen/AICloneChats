import { Link, useNavigate } from "react-router-dom";
import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";

export default function Navbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Public navigation — five anchors, no operator tooling. The previous
  // navbar had become an internal-tools dashboard; the admin/observability
  // surfaces now live exclusively under /admin and are reachable only via
  // the Admin index when role === "admin".
  const navLinkClass = "font-display font-bold text-sm text-ink/80 hover:text-ink transition";

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
        <nav className="hidden md:flex items-center gap-4 lg:gap-5 flex-shrink-0" data-testid="nav-desktop">
          <Link to="/explore" className={navLinkClass} data-testid="nav-explore">Explore</Link>
          <Link to="/debates" className={navLinkClass} data-testid="nav-debates">Debates</Link>
          {user && (
            <Link to="/dashboard" className={navLinkClass} data-testid="nav-dashboard">Dashboard</Link>
          )}
          {user?.role === "admin" && (
            <Link to="/admin" className="font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin">
              Admin
            </Link>
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
          aria-label="Toggle navigation"
          data-testid="nav-mobile-toggle"
        >
          <span className="block w-4 leading-none text-base">{mobileOpen ? "✕" : "☰"}</span>
        </button>
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden border-t border-white/5 bg-bg/95 backdrop-blur-xl" data-testid="nav-mobile-drawer">
          <div className="max-w-6xl mx-auto px-4 sm:px-5 py-3 flex flex-col gap-1">
            <Link to="/explore" onClick={() => setMobileOpen(false)} className="py-2 text-sm font-display font-bold text-ink/85" data-testid="nav-mobile-explore">Explore</Link>
            <Link to="/debates" onClick={() => setMobileOpen(false)} className="py-2 text-sm font-display font-bold text-ink/85" data-testid="nav-mobile-debates">Debates</Link>
            {user && (
              <Link to="/dashboard" onClick={() => setMobileOpen(false)} className="py-2 text-sm font-display font-bold text-ink/85" data-testid="nav-mobile-dashboard">Dashboard</Link>
            )}
            {user?.role === "admin" && (
              <Link to="/admin" onClick={() => setMobileOpen(false)} className="py-2 text-sm font-display font-bold text-violet-soft" data-testid="nav-mobile-admin">Admin</Link>
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
                  <Link to="/login" onClick={() => setMobileOpen(false)} className="btn-ghost text-xs flex-1 text-center" data-testid="nav-mobile-login">Log in</Link>
                  <Link to="/register" onClick={() => setMobileOpen(false)} className="btn-brutal text-xs flex-1 text-center" data-testid="nav-mobile-signup">Get started</Link>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
