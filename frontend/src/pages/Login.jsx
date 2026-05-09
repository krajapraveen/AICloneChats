import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";
import GoogleSignInButton from "../components/GoogleSignInButton";

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
      let detail = err?.response?.data?.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      if (!detail) {
        if (err?.message === "Network Error") detail = "Network error — please check your connection.";
        else if (err?.response?.status) detail = `Login failed (HTTP ${err.response.status})`;
        else detail = "Login failed";
      }
      setError(detail);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[380px] h-[380px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[400px] h-[400px] top-40 -left-32 opacity-25 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />
      <div className="max-w-md mx-auto px-4 sm:px-5 md:px-8 py-12 sm:py-16 relative">
        <div className="brutal-card p-8" data-testid="login-card">
          <h1 className="heading-display text-4xl mb-2">Welcome back.</h1>
          <p className="text-sm text-muted mb-7 font-medium">Talk to your AI self again.</p>

          <GoogleSignInButton testId="login-google-btn" />

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
