import { useEffect, useMemo, useState } from "react";
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

// Mirrors backend _password_is_strong rules. Used only for live UX feedback;
// the backend is the source of truth and re-validates every submission.
const RULES = [
  { id: "len", label: "At least 8 characters", test: (s) => s.length >= 8 },
  { id: "upper", label: "At least one uppercase letter (A–Z)", test: (s) => /[A-Z]/.test(s) },
  { id: "lower", label: "At least one lowercase letter (a–z)", test: (s) => /[a-z]/.test(s) },
  { id: "digit", label: "At least one number (0–9)", test: (s) => /\d/.test(s) },
  { id: "special", label: "At least one special character (!@#$…)", test: (s) => /[^A-Za-z0-9\s]/.test(s) },
  { id: "nospace", label: "No spaces", test: (s) => !!s && !/\s/.test(s) },
];

function RuleRow({ ok, label, testId }) {
  return (
    <li className="flex items-start gap-2 text-xs" data-testid={testId} data-ok={ok ? "1" : "0"}>
      <span
        aria-hidden
        className={`inline-flex items-center justify-center w-4 h-4 rounded-full mt-0.5 ${
          ok ? "bg-emerald-500/25 text-emerald-300 border border-emerald-500/40" : "bg-white/5 text-muted border border-white/10"
        }`}
      >
        {ok ? "✓" : "•"}
      </span>
      <span className={ok ? "text-emerald-200" : "text-muted"}>{label}</span>
    </li>
  );
}

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [validating, setValidating] = useState(true);
  const [tokenStatus, setTokenStatus] = useState({ valid: false, code: "" });
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [touchedPw, setTouchedPw] = useState(false);
  const [touchedConfirm, setTouchedConfirm] = useState(false);
  const [serverError, setServerError] = useState("");
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

  const ruleStatus = useMemo(() => RULES.map((r) => ({ ...r, ok: r.test(pw) })), [pw]);
  const allRulesPass = ruleStatus.every((r) => r.ok);
  const matches = pw === confirm && confirm.length > 0;
  const canSubmit = allRulesPass && matches && !loading;

  const onSubmit = async (e) => {
    e.preventDefault();
    setServerError("");
    setTouchedPw(true);
    setTouchedConfirm(true);
    if (!allRulesPass) return;
    if (!matches) return;
    setLoading(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: pw, confirm_password: confirm });
      setDone(true);
      setTimeout(() => navigate("/login"), 2500);
    } catch (err) {
      setServerError(formatAuthError(err));
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
                <div className="text-[10px] font-mono uppercase tracking-widest text-rose-soft mb-1">
                  {tokenStatus.code === "token_expired" ? "Link expired" : "Link invalid"}
                </div>
                <p className="font-medium">
                  {tokenStatus.code === "token_expired"
                    ? "This reset link has expired. Request a fresh one."
                    : "This reset link is invalid or has already been used."}
                </p>
              </div>
              <Link to="/forgot-password" className="btn-brutal text-xs w-full block text-center" data-testid="reset-request-new">Request a new link</Link>
              <Link to="/login" className="btn-ghost text-xs w-full block text-center" data-testid="reset-back-login">Back to sign in</Link>
            </div>
          )}

          {!validating && tokenStatus.valid && !done && (
            <form onSubmit={onSubmit} className="space-y-4" data-testid="reset-form" noValidate>
              <div>
                <label htmlFor="reset-pw" className="label-brutal block mb-1.5">New password</label>
                <input
                  id="reset-pw"
                  className="input-brutal"
                  type="password"
                  required
                  value={pw}
                  onChange={(e) => setPw(e.target.value)}
                  onBlur={() => setTouchedPw(true)}
                  placeholder="••••••••"
                  data-testid="reset-pw-input"
                  autoComplete="new-password"
                  aria-invalid={touchedPw && !allRulesPass}
                  aria-describedby="reset-pw-rules"
                />
                <ul id="reset-pw-rules" className="mt-3 space-y-1.5" data-testid="reset-pw-rules">
                  {ruleStatus.map((r) => (
                    <RuleRow key={r.id} ok={r.ok} label={r.label} testId={`reset-rule-${r.id}`} />
                  ))}
                </ul>
              </div>

              <div>
                <label htmlFor="reset-confirm" className="label-brutal block mb-1.5">Re-type new password</label>
                <input
                  id="reset-confirm"
                  className="input-brutal"
                  type="password"
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  onBlur={() => setTouchedConfirm(true)}
                  placeholder="••••••••"
                  data-testid="reset-confirm-input"
                  autoComplete="new-password"
                  aria-invalid={touchedConfirm && confirm.length > 0 && !matches}
                  aria-describedby="reset-confirm-error"
                />
                {touchedConfirm && confirm.length > 0 && !matches && (
                  <p id="reset-confirm-error" className="text-xs text-rose-soft mt-1.5 font-medium" data-testid="reset-confirm-mismatch">
                    Passwords do not match.
                  </p>
                )}
                {touchedConfirm && matches && (
                  <p className="text-xs text-emerald-300 mt-1.5 font-medium" data-testid="reset-confirm-match">
                    Passwords match.
                  </p>
                )}
              </div>

              {serverError && (
                <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="reset-error">
                  {serverError}
                </div>
              )}

              <button
                type="submit"
                disabled={!canSubmit}
                className="btn-brutal w-full disabled:opacity-50 disabled:cursor-not-allowed"
                data-testid="reset-submit-btn"
              >
                {loading ? "Updating…" : "Update password"}
              </button>
              <p className="text-xs text-muted text-center">
                You'll be signed out everywhere and asked to sign in with your new password.
              </p>
            </form>
          )}

          {done && (
            <div className="space-y-3" data-testid="reset-success">
              <div className="bg-amber/10 border border-amber/40 rounded-xl px-4 py-3 text-sm">
                <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1">Password updated</div>
                <p className="font-medium">A confirmation email has been sent. All your old sessions have been signed out. Redirecting to sign in…</p>
              </div>
              <Link to="/login" className="btn-ghost text-xs w-full block text-center" data-testid="reset-go-to-login">Go to sign in now</Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
