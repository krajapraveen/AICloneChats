import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export default function Navbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

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

        <nav className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
          <Link to="/explore" className="hidden md:inline-block font-display font-bold text-sm text-ink/80 hover:text-amber-soft transition" data-testid="nav-explore">
            Explore
          </Link>
          <Link to="/smart-reply" className="hidden md:inline-block font-display font-bold text-sm text-ink/80 hover:text-emerald-soft transition" data-testid="nav-smart-reply">
            Smart Reply
          </Link>
          <Link to="/voice" className="hidden md:inline-block font-display font-bold text-sm text-ink/80 hover:text-emerald-soft transition" data-testid="nav-voice">
            Voice
          </Link>
          <Link to="/anonymous-reality" className="hidden md:inline-block font-display font-bold text-sm text-ink/80 hover:text-rose-300 transition" data-testid="nav-anonymous">
            Anonymous
          </Link>
          <Link to="/debates" className="hidden md:inline-block font-display font-bold text-sm text-ink/80 hover:text-sky-300 transition" data-testid="nav-debates">
            Debates
          </Link>
          {user ? (
            <>
              <Link to="/dashboard" className="hidden sm:inline-block font-display font-bold text-sm text-ink/80 hover:text-ink transition" data-testid="nav-dashboard">
                Dashboard
              </Link>
              {user.role === "admin" && (
                <>
                  <Link to="/admin/login-intelligence" className="hidden md:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin">
                    Admin
                  </Link>
                  <Link to="/admin/voice-metrics" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-voice-metrics">
                    Voice Metrics
                  </Link>
                  <Link to="/admin/anonymous-reality" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-anonymous">
                    Anon Mod
                  </Link>
                  <Link to="/admin/anonymous-metrics" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-anonymous-metrics">
                    Anon Metrics
                  </Link>
                  <Link to="/admin/debates" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-debates">
                    Debates Mod
                  </Link>
                  <Link to="/admin/debates/retention" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-debates-retention">
                    Debates Retention
                  </Link>
                  <Link to="/admin/safety" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-safety">
                    Safety
                  </Link>
                  <Link to="/admin/chats" className="hidden lg:inline-block font-display font-bold text-sm text-violet-soft hover:text-violet transition" data-testid="nav-admin-chats">
                    Chats
                  </Link>
                </>
              )}
              <span className="hidden lg:inline-block text-xs font-mono text-muted truncate max-w-[180px]" data-testid="nav-user-email">
                {user.email}
              </span>
              <button onClick={async () => { await logout(); navigate("/"); }} className="btn-ghost text-xs sm:text-sm" data-testid="nav-logout">
                Log out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className="btn-ghost text-xs sm:text-sm" data-testid="nav-login">Log in</Link>
              <Link to="/register" className="btn-brutal text-xs sm:text-sm" data-testid="nav-signup">Get started</Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
