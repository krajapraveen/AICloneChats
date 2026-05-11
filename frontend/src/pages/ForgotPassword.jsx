import { useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";

function formatAuthError(err) {
  const detail = err?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) return detail.message || detail.code || "Request failed";
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  if (typeof detail === "string") return detail;
  if (err?.message === "Network Error") return "Network error — please check your connection.";
  return "Request failed.";
}

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.post("/auth/forgot-password", { email });
      setSubmitted(true);
    } catch (err) {
      setError(formatAuthError(err));
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
        <div className="brutal-card p-8" data-testid="forgot-card">
          <h1 className="heading-display text-3xl mb-2">Forgot your password?</h1>
          <p className="text-sm text-muted mb-6 font-medium">
            Enter your email. If we have an account on file, we'll send a reset link that expires in 30 minutes.
          </p>

          {submitted ? (
            <div className="space-y-4">
              <div className="bg-amber/10 border border-amber/40 rounded-xl px-4 py-3 text-sm" data-testid="forgot-success">
                <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1">Check your inbox</div>
                <p className="font-medium">If this email exists, reset instructions have been sent.</p>
                <p className="text-muted mt-1 text-xs">Link expires in 30 minutes. Didn't get it? Check spam or wait a couple of minutes before requesting again.</p>
              </div>
              <Link to="/login" className="btn-ghost text-xs w-full block text-center" data-testid="forgot-back-to-login">Back to sign in</Link>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4" data-testid="forgot-form">
              <div>
                <label className="label-brutal block mb-1.5">Email</label>
                <input
                  className="input-brutal"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  data-testid="forgot-email-input"
                  autoComplete="email"
                />
              </div>
              {error && (
                <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="forgot-error">
                  {error}
                </div>
              )}
              <button type="submit" disabled={loading} className="btn-brutal w-full" data-testid="forgot-submit-btn">
                {loading ? "Sending…" : "Send reset link"}
              </button>
              <p className="text-sm text-center font-medium text-muted">
                Remembered it? <Link className="font-bold underline underline-offset-2 text-ink" to="/login" data-testid="forgot-to-login">Back to sign in</Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
