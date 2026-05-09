import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export default function Navbar({ variant = "default" }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="border-b-2 border-ink bg-cream sticky top-0 z-40" data-testid="navbar">
      <div className="max-w-6xl mx-auto px-5 md:px-8 py-4 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2 group" data-testid="nav-logo">
          <div className="w-9 h-9 bg-lemon border-2 border-ink rounded-xl shadow-brutal-sm flex items-center justify-center font-display font-black text-lg">
            C
          </div>
          <span className="font-display font-black text-xl tracking-tight">CloneMe<span className="text-bubblegum">.</span>AI</span>
        </Link>

        <nav className="flex items-center gap-3">
          {user ? (
            <>
              <Link to="/dashboard" className="hidden sm:inline-block font-display font-bold text-sm hover:underline underline-offset-4" data-testid="nav-dashboard">
                Dashboard
              </Link>
              <span className="hidden md:inline-block text-xs font-mono text-muted-foreground" data-testid="nav-user-email">
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
