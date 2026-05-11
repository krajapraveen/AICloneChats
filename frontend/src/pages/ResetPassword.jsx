import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
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

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [validating, setValidating] = useState(true);
  const [tokenStatus, setTokenStatus] = useState({ valid: false, code: "" });
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!token) {
        if (!cancelled) {
          setTokenStatus({ valid: false, code: "token_missing" });
          setValidating(false);
        }
        return;
      }
      try {
        const { data } = await api.get(`/auth/reset-password/validate?token=${encodeURIComponent(token)}`);
        if (!cancelled) setTokenStatus({ valid: !!data.valid, code: data.code || "" });
      } catch {
        if (!cancelled) setTokenStatus({ valid: false, code: "token_invalid" });
      } finally {
        if (!cancelled) setValidating(false);
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (pw !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (pw.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setLoading(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: pw, confirm_password: confirm });
      setDone(true);
      setTimeout(() => navigate("/login"), 2500);
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
        <div className="brutal-card p-8" data-testid="reset-card">
          <h1 className="heading-display text-3xl mb-2">Set a new password</h1>
          {validating && (
            <p className="text-sm text-muted font-medium" data-testid="reset-validating">Validating reset link…</p>
          )}
          {!validating && !tokenStatus.valid && !done && (
            <div className="space-y-4" data-testid="reset-invalid">
              <div className="bg-rose/15 border border-rose/40 rounded-xl px-4 py-3 text-sm">
                <div className="text-[10px] font-mono uppercase tracking-widest text-rose-soft mb-1">{tokenStatus.code === "token_expired" ? "Link expired" : "Link invalid"}</div>
                <p className="font-medium">This reset link is no longer valid. Request a fresh one.</p>
              </div>
              <Link to="/forgot-password" className="btn-brutal text-xs w-full block text-center" data-testid="reset-request-new">Request a new link</Link>
              <Link to="/login" className="btn-ghost text-xs w-full block text-center" data-testid="reset-back-login">Back to sign in</Link>
            </div>
          )}
          {!validating && tokenStatus.valid && !done && (
            <form onSubmit={onSubmit} className="space-y-4" data-testid="reset-form">
              <p className="text-sm text-muted font-medium mb-2">Pick a password with 8+ characters, mix of cases, and a digit.</p>
              <div>
                <label className="label-brutal block mb-1.5">New password</label>
                <input
                  className="input-brutal"
                  type="password"
                  required
                  minLength={8}
                  value={pw}
                  onChange={(e) => setPw(e.target.value)}
                  placeholder="••••••••"
                  data-testid="reset-pw-input"
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label className="label-brutal block mb-1.5">Confirm new password</label>
                <input
                  className="input-brutal"
                  type="password"
                  required
                  minLength={8}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="••••••••"
                  data-testid="reset-confirm-input"
                  autoComplete="new-password"
                />
              </div>
              {error && (
                <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="reset-error">
                  {error}
                </div>
              )}
              <button type="submit" disabled={loading} className="btn-brutal w-full" data-testid="reset-submit-btn">
                {loading ? "Updating…" : "Update password"}
              </button>
            </form>
          )}
          {done && (
            <div className="space-y-3" data-testid="reset-success">
              <div className="bg-amber/10 border border-amber/40 rounded-xl px-4 py-3 text-sm">
                <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1">Password updated</div>
                <p className="font-medium">All your old sessions have been signed out. Redirecting to sign in…</p>
              </div>
              <Link to="/login" className="btn-ghost text-xs w-full block text-center">Go to sign in now</Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
