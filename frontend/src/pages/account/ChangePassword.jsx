import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../../lib/api";

const RULES = [
  { id: "len", label: "At least 8 characters", test: (s) => s.length >= 8 },
  { id: "upper", label: "At least one uppercase letter (A–Z)", test: (s) => /[A-Z]/.test(s) },
  { id: "lower", label: "At least one lowercase letter (a–z)", test: (s) => /[a-z]/.test(s) },
  { id: "digit", label: "At least one number (0–9)", test: (s) => /\d/.test(s) },
  { id: "special", label: "At least one special character (!@#$…)", test: (s) => /[^A-Za-z0-9\s]/.test(s) },
  { id: "nospace", label: "No spaces", test: (s) => !!s && !/\s/.test(s) },
];

export default function ChangePassword() {
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [serverError, setServerError] = useState("");
  const [done, setDone] = useState(false);

  const ruleStatus = RULES.map((r) => ({ ...r, ok: r.test(pw) }));
  const allRulesPass = ruleStatus.every((r) => r.ok);
  const matches = pw === confirm && confirm.length > 0;
  const canSubmit = current.length > 0 && allRulesPass && matches && !loading;

  const onSubmit = async (e) => {
    e.preventDefault();
    setServerError("");
    if (!canSubmit) return;
    setLoading(true);
    try {
      await api.post("/auth/change-password", {
        current_password: current,
        new_password: pw,
        confirm_password: confirm,
      });
      setDone(true);
      toast.success("Password updated. Signing you out…");
      // backend invalidated other sessions; force a fresh sign-in
      setTimeout(() => navigate("/login"), 1800);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setServerError(
        typeof detail === "object" ? detail.message || detail.code || "Update failed" :
        typeof detail === "string" ? detail :
        "Update failed."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <section data-testid="change-password-section">
      <h2 className="heading-display text-2xl mb-1">Change Password</h2>
      <p className="text-sm text-muted mb-6">All other sessions on every device will be signed out after a successful change.</p>

      {done ? (
        <div className="brutal-card p-6 bg-amber/5 border border-amber/40" data-testid="change-password-success">
          <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1">Updated</div>
          <p className="text-sm">Your password was changed and a confirmation email has been sent. Redirecting to sign in…</p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="brutal-card p-6 space-y-4 max-w-lg" data-testid="change-password-form" noValidate>
          <div>
            <label htmlFor="cp-current" className="label-brutal block mb-1.5">Current password</label>
            <input id="cp-current" type="password" className="input-brutal" value={current}
                   onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password"
                   data-testid="cp-current-input" required />
          </div>

          <div>
            <label htmlFor="cp-new" className="label-brutal block mb-1.5">New password</label>
            <input id="cp-new" type="password" className="input-brutal" value={pw}
                   onChange={(e) => setPw(e.target.value)} autoComplete="new-password"
                   data-testid="cp-new-input" required />
            <ul className="mt-3 space-y-1.5" data-testid="cp-rules">
              {ruleStatus.map((r) => (
                <li key={r.id} className="flex items-start gap-2 text-xs" data-testid={`cp-rule-${r.id}`} data-ok={r.ok ? "1" : "0"}>
                  <span aria-hidden className={`inline-flex items-center justify-center w-4 h-4 rounded-full mt-0.5 ${
                    r.ok ? "bg-emerald-500/25 text-emerald-300 border border-emerald-500/40" : "bg-white/5 text-muted border border-white/10"
                  }`}>{r.ok ? "✓" : "•"}</span>
                  <span className={r.ok ? "text-emerald-200" : "text-muted"}>{r.label}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <label htmlFor="cp-confirm" className="label-brutal block mb-1.5">Re-type new password</label>
            <input id="cp-confirm" type="password" className="input-brutal" value={confirm}
                   onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password"
                   data-testid="cp-confirm-input" required />
            {confirm && !matches && (
              <p className="text-xs text-rose-soft mt-1.5 font-medium" data-testid="cp-mismatch">
                Passwords do not match.
              </p>
            )}
            {confirm && matches && (
              <p className="text-xs text-emerald-300 mt-1.5 font-medium" data-testid="cp-match">
                Passwords match.
              </p>
            )}
          </div>

          {serverError && (
            <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="cp-error">
              {serverError}
            </div>
          )}

          <button type="submit" disabled={!canSubmit}
                  className="btn-brutal w-full disabled:opacity-50 disabled:cursor-not-allowed"
                  data-testid="cp-submit-btn">
            {loading ? "Updating…" : "Update password"}
          </button>
        </form>
      )}
    </section>
  );
}
