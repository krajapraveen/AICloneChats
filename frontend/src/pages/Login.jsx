import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
function startGoogleAuth() {
  const redirectUrl = window.location.origin + "/auth/callback";
  window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
}

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const oauthError = searchParams.get("error");

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err) {
      setError(err?.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[380px] h-[380px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[400px] h-[400px] top-40 -left-32 opacity-25 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />
      <div className="max-w-md mx-auto px-5 md:px-8 py-16 relative">
        <div className="brutal-card p-8" data-testid="login-card">
          <h1 className="heading-display text-4xl mb-2">Welcome back.</h1>
          <p className="text-sm text-muted mb-7 font-medium">Talk to your AI self again.</p>

          <button onClick={startGoogleAuth} className="btn-ghost w-full mb-5" data-testid="login-google-btn">
            <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.6-6 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34.5 6.5 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3 0 5.8 1.1 7.9 3l5.7-5.7C34.5 6.5 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.4 0 10.3-2.1 14-5.4l-6.5-5.5c-2 1.5-4.6 2.4-7.5 2.4-5.3 0-9.7-3.4-11.3-8l-6.5 5C9.6 39.6 16.3 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.4 4.3-4.5 5.7l6.5 5.5c4.6-4.2 7.7-10.5 7.7-17.7 0-1.3-.1-2.7-.4-3.5z"/></svg>
            Continue with Google
          </button>

          <div className="flex items-center gap-3 my-5">
            <div className="flex-1 h-px bg-white/10"></div>
            <span className="font-mono text-xs uppercase tracking-widest text-muted">or</span>
            <div className="flex-1 h-px bg-white/10"></div>
          </div>

          <form onSubmit={onSubmit} className="space-y-4" data-testid="login-form">
            <div>
              <label className="label-brutal block mb-1.5">Email</label>
              <input className="input-brutal" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" data-testid="login-email-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Password</label>
              <input className="input-brutal" type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" data-testid="login-password-input" />
            </div>
            {(error || oauthError) && (
              <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="login-error">
                {error || "Google sign-in failed. Try again?"}
              </div>
            )}
            <button type="submit" disabled={loading} className="btn-brutal w-full" data-testid="login-submit-btn">
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <p className="text-sm text-center mt-6 font-medium">
            New here? <Link className="font-bold underline underline-offset-2" to="/register" data-testid="login-to-register">Make an account</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
