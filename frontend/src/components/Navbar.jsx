import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export default function Navbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="border-b border-white/5 bg-bg/60 sticky top-0 z-40 backdrop-blur-xl" data-testid="navbar">
      <div className="max-w-6xl mx-auto px-5 md:px-8 py-4 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2.5 group" data-testid="nav-logo">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-lg shadow-glow-amber">
            C
          </div>
          <span className="font-display font-black text-xl tracking-tight text-ink">CloneMe<span className="text-amber">.</span>AI</span>
        </Link>

        <nav className="flex items-center gap-3">
          <Link to="/explore" className="hidden sm:inline-block font-display font-bold text-sm text-ink/80 hover:text-amber-soft transition" data-testid="nav-explore">
            Explore
          </Link>
          <Link to="/smart-reply" className="hidden sm:inline-block font-display font-bold text-sm text-ink/80 hover:text-emerald-soft transition" data-testid="nav-smart-reply">
            Smart Reply
          </Link>
          {user ? (
            <>
              <Link to="/dashboard" className="hidden sm:inline-block font-display font-bold text-sm text-ink/80 hover:text-ink transition" data-testid="nav-dashboard">
                Dashboard
              </Link>
              <span className="hidden md:inline-block text-xs font-mono text-muted" data-testid="nav-user-email">
                {user.email}
              </span>
              <button onClick={async () => { await logout(); navigate("/"); }} className="btn-ghost text-sm" data-testid="nav-logout">
                Log out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className="btn-ghost text-sm" data-testid="nav-login">Log in</Link>
              <Link to="/register" className="btn-brutal text-sm" data-testid="nav-signup">Get started</Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
