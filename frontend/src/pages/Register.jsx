import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { useGoogleAuthConfig } from "../contexts/GoogleAuthConfigContext";
import Navbar from "../components/Navbar";
import GoogleSignInButton from "../components/GoogleSignInButton";

export default function Register() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();
  const { configured: googleConfigured } = useGoogleAuthConfig();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await register(email, password, name);
      const next = searchParams.get("next") || "/dashboard";
      navigate(next, { replace: true });
    } catch (err) {
      let detail = err?.response?.data?.detail;
      if (detail && typeof detail === "object" && !Array.isArray(detail)) detail = detail.message || detail.code;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      if (!detail) {
        if (err?.message === "Network Error") detail = "Network error — please check your connection and try again.";
        else if (err?.response?.status) detail = `Sign up failed (HTTP ${err.response.status})`;
        else detail = "Sign up failed";
      }
      setError(detail);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-violet w-[400px] h-[400px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-amber w-[380px] h-[380px] top-40 -left-32 opacity-25 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />
      <div className="max-w-md mx-auto px-4 sm:px-5 md:px-8 py-12 sm:py-16 relative">
        <div className="brutal-card p-8" data-testid="register-card">
          <h1 className="heading-display text-4xl mb-2">Build your clone.</h1>
          <p className="text-sm text-muted mb-7 font-medium">3 minutes. No credit card. No vibes harmed.</p>

          <GoogleSignInButton testId="register-google-btn" label="Sign up with Google" />

          {googleConfigured && (
            <div className="flex items-center gap-3 my-5">
              <div className="flex-1 h-px bg-white/10"></div>
              <span className="font-mono text-xs uppercase tracking-widest text-muted">or</span>
              <div className="flex-1 h-px bg-white/10"></div>
            </div>
          )}

          <form onSubmit={onSubmit} className="space-y-4" data-testid="register-form">
            <div>
              <label className="label-brutal block mb-1.5">Name</label>
              <input className="input-brutal" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name (or alias)" data-testid="register-name-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Email</label>
              <input className="input-brutal" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" data-testid="register-email-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Password</label>
              <input className="input-brutal" type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="6+ characters" data-testid="register-password-input" />
            </div>
            {error && (
              <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="register-error">
                {error}
              </div>
            )}
            <button type="submit" disabled={loading} className="btn-brutal w-full" data-testid="register-submit-btn">
              {loading ? "Creating…" : "Create my account"}
            </button>
            <p className="text-[10px] font-mono text-muted/80 leading-relaxed text-center" data-testid="register-privacy-notice">
              By creating an account you acknowledge that chats may be reviewed by platform administrators for safety, abuse prevention, and service improvement. Sensitive values (emails, phones, passwords, API keys) are auto-redacted before review.
            </p>
          </form>

          <p className="text-sm text-center mt-6 font-medium">
            Already have one? <Link className="font-bold underline underline-offset-2" to="/login" data-testid="register-to-login">Log in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
