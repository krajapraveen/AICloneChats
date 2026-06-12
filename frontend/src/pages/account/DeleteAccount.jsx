import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../../lib/api";
import { useAuth } from "../../contexts/AuthContext";

const REQUIRED_PHRASE = "delete my account";

export default function DeleteAccount() {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const isGoogle = (user?.auth_provider || "").toLowerCase() === "google";

  const [password, setPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [loading, setLoading] = useState(false);
  const [serverError, setServerError] = useState("");
  const [done, setDone] = useState(false);

  // Export-side state
  const [exportCounts, setExportCounts] = useState(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let cancel = false;
    api.get("/profile/export/preview")
      .then((r) => !cancel && setExportCounts(r.data?.counts || null))
      .catch(() => {});
    return () => { cancel = true; };
  }, []);

  const onDownload = async () => {
    setExporting(true);
    try {
      const r = await api.get("/profile/export", { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([r.data], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      const cd = r.headers?.["content-disposition"] || "";
      const m = cd.match(/filename="([^"]+)"/);
      a.download = m ? m[1] : `aiclonechats-export-${Date.now()}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success("Your data has been downloaded.");
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const code = typeof detail === "object" ? detail?.code : null;
      const msg = code === "rate_limited"
        ? "You've exported recently. Try again in a few minutes."
        : "Could not download your data right now. Please try again.";
      toast.error(msg);
    } finally {
      setExporting(false);
    }
  };

  const phraseOk = phrase.trim().toLowerCase() === REQUIRED_PHRASE;
  const pwOk = isGoogle ? true : password.length > 0;
  const canSubmit = phraseOk && pwOk && acknowledged && !loading;

  const onSubmit = async (e) => {
    e.preventDefault();
    setServerError("");
    if (!canSubmit) return;
    setLoading(true);
    try {
      const body = { confirm: true, reason: reason.trim() || null };
      if (!isGoogle) body.password = password;
      await api.post("/profile/delete-account", body);
      setDone(true);
      toast.success("Account deleted. Signing you out…");
      setTimeout(async () => {
        try { await logout?.(); } catch (_e) { /* best-effort */ }
        navigate("/", { replace: true });
      }, 2000);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const msg =
        typeof detail === "object" ? (detail.message || detail.code || "Deletion failed") :
        typeof detail === "string" ? detail : "Deletion failed.";
      setServerError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section data-testid="delete-account-section">
      <h2 className="heading-display text-2xl mb-1">Privacy &amp; Data</h2>
      <p className="text-sm text-muted mb-6">
        Download a portable copy of your data, or permanently delete your account.
      </p>

      {/* ── Data Export ─────────────────────────────────────── */}
      <div className="brutal-card p-6 mb-8 max-w-lg" data-testid="export-section">
        <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1.5">Export your data</div>
        <h3 className="text-base font-semibold mb-1.5">Download everything we have about you</h3>
        <p className="text-sm text-muted mb-4">
          A single JSON file containing your profile, clones, memories, support threads,
          payments, and message history. Conforms to GDPR Article 20 / DPDP portability.
        </p>
        {exportCounts && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4 text-xs" data-testid="export-counts">
            {Object.entries(exportCounts).map(([key, val]) => (
              <div key={key} className="bg-white/[0.03] border border-white/10 rounded-lg px-3 py-2">
                <div className="text-muted text-[10px] uppercase tracking-wider">{key.replaceAll("_", " ")}</div>
                <div className="font-semibold text-ink">{val}</div>
              </div>
            ))}
          </div>
        )}
        <button
          type="button"
          onClick={onDownload}
          disabled={exporting}
          className="btn-brutal disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="export-download-btn"
        >
          {exporting ? "Preparing download…" : "Download my data (.json)"}
        </button>
      </div>

      <h3 className="heading-display text-xl mb-1">Delete account</h3>
      <p className="text-sm text-muted mb-4">
        Permanently delete your account and personal data. This action cannot be undone.
      </p>

      {done ? (
        <div className="brutal-card p-6 bg-rose/10 border border-rose/40" data-testid="delete-success">
          <div className="text-[10px] font-mono uppercase tracking-widest text-rose-soft mb-1">Deleted</div>
          <p className="text-sm">Your account has been deleted. Signing you out…</p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="brutal-card p-6 space-y-5 max-w-lg" data-testid="delete-account-form" noValidate>
          <div className="rounded-xl border border-rose/40 bg-rose/10 px-4 py-3.5 text-sm leading-relaxed" data-testid="delete-warning-box">
            <div className="text-[10px] font-mono uppercase tracking-widest text-rose-soft mb-1.5">Warning · permanent</div>
            <p className="mb-2">When you delete your account we will <strong>immediately</strong>:</p>
            <ul className="space-y-1 pl-4 list-disc text-ink/85">
              <li>Erase your email, name, picture, and password.</li>
              <li>End every active session on every device.</li>
              <li>Unpublish every clone you created.</li>
              <li>Delete personal memories you uploaded.</li>
            </ul>
            <p className="mt-3 text-muted text-xs">
              Anonymized payment and support records are retained for legal and tax compliance only.
              You can re-create a new account with this email any time later.
            </p>
          </div>

          {!isGoogle && (
            <div>
              <label htmlFor="del-password" className="label-brutal block mb-1.5">Confirm your password</label>
              <input
                id="del-password"
                type="password"
                className="input-brutal"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                data-testid="delete-password-input"
                required
              />
            </div>
          )}

          <div>
            <label htmlFor="del-phrase" className="label-brutal block mb-1.5">
              Type <span className="text-rose-soft font-mono">{REQUIRED_PHRASE}</span> to confirm
            </label>
            <input
              id="del-phrase"
              type="text"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              className="input-brutal font-mono"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              data-testid="delete-phrase-input"
              placeholder={REQUIRED_PHRASE}
              required
            />
          </div>

          <div>
            <label htmlFor="del-reason" className="label-brutal block mb-1.5">Reason (optional)</label>
            <textarea
              id="del-reason"
              rows={2}
              maxLength={500}
              className="input-brutal resize-none"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Help us improve — what made you leave?"
              data-testid="delete-reason-input"
            />
          </div>

          <label className="flex items-start gap-2.5 text-sm cursor-pointer" data-testid="delete-ack-row">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              className="mt-0.5 w-4 h-4 accent-rose-soft"
              data-testid="delete-ack-checkbox"
            />
            <span className="text-ink/85">
              I understand this is permanent and cannot be undone.
            </span>
          </label>

          {serverError && (
            <div className="bg-rose/15 border border-rose/40 text-rose-soft rounded-xl px-4 py-2.5 text-sm font-medium" data-testid="delete-error">
              {serverError}
            </div>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full px-5 py-3 rounded-xl bg-rose-soft text-black font-semibold text-sm uppercase tracking-wider disabled:opacity-40 disabled:cursor-not-allowed hover:bg-rose transition"
            data-testid="delete-submit-btn"
          >
            {loading ? "Deleting…" : "Permanently delete my account"}
          </button>
        </form>
      )}
    </section>
  );
}
