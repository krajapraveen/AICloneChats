/**
 * Admin · Enforce Zero-Credit Policy
 *
 * One-screen operator surface for the production backfill that sweeps and
 * zero-outs stray `credits_balance` for non-admin, non-subscriber users.
 *
 * Flow:
 *   1. Operator opens the page → automatic dry-run reports how many users
 *      WOULD be affected + total credits at stake.
 *   2. Operator inspects the sample (first 20 users) + skipped counts.
 *   3. Operator clicks "Run for real" → confirmation modal → real sweep.
 *   4. Audit row (admin_adjust + surface=enforce_zero_policy) is written
 *      per user. Admins and active subscribers are never touched.
 */
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function Tile({ label, value, tone = "ink" }) {
  return (
    <div className="brutal-card p-4">
      <div className="text-[10px] uppercase tracking-widest text-muted font-mono mb-1">{label}</div>
      <div className={`text-2xl font-display font-bold text-${tone}`}>{value}</div>
    </div>
  );
}

export default function AdminEnforceZeroCreditPolicy() {
  const { user, loading } = useAuth();
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [lastRun, setLastRun] = useState(null);

  async function runDryRun() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/admin/billing/enforce-zero-credit-policy", {
        dry_run: true,
      });
      setPreview(data);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || e?.message || "Dry-run failed");
    } finally {
      setBusy(false);
    }
  }

  async function runForReal() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/admin/billing/enforce-zero-credit-policy", {
        dry_run: false,
        reason: "enforce_zero_policy",
      });
      setLastRun(data);
      setConfirmOpen(false);
      setConfirmText("");
      // Re-pull dry-run to refresh remaining count.
      await runDryRun();
    } catch (e) {
      setError(e?.response?.data?.detail?.message || e?.message || "Sweep failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (user?.role !== "admin") return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.post("/admin/billing/enforce-zero-credit-policy", { dry_run: true });
        if (!cancelled) setPreview(data);
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail?.message || e?.message || "Dry-run failed");
      }
    })();
    return () => { cancelled = true; };
  }, [user?.role]);

  if (loading) {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-5xl mx-auto px-4 sm:px-8 py-10 text-muted font-mono text-sm">Loading…</div>
      </div>
    );
  }
  if (!user) return <Navigate to="/login?redirect=/admin/enforce-zero-credit-policy" replace />;
  if (user.role !== "admin") return <Navigate to="/admin" replace />;

  return (
    <div className="min-h-screen page-bg">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 sm:px-8 py-10 space-y-8" data-testid="admin-enforce-zero-policy">
        <header className="space-y-2">
          <div className="text-amber-300 font-mono text-xs uppercase tracking-widest">Production backfill</div>
          <h1 className="font-display text-3xl sm:text-4xl font-bold text-ink">Enforce zero-credit policy</h1>
          <p className="text-sm text-muted max-w-2xl">
            The platform enforces a strict 0-credit policy for free users — no signup grants, no daily free
            allowance. Subscribers and admin-unlimited accounts are the only ones who carry credits. This
            sweep zeroes any stray balances that pre-date the policy. It does NOT touch active subscribers
            or admin-unlimited accounts. Each user&apos;s reset is recorded in <code className="text-xs">credit_events</code>{" "}
            as <code className="text-xs">admin_adjust · enforce_zero_policy</code>.
          </p>
        </header>

        {error && (
          <div className="brutal-card border-rose/40 bg-rose-500/10 p-4 text-sm text-rose-200" data-testid="admin-enforce-error">
            {error}
          </div>
        )}

        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-lg text-ink">Current state (dry-run)</h2>
            <button
              type="button"
              onClick={runDryRun}
              disabled={busy}
              className="text-xs font-mono uppercase tracking-widest text-violet-300 hover:text-violet-100 disabled:opacity-40"
              data-testid="admin-enforce-refresh"
            >
              {busy ? "…" : "Refresh"}
            </button>
          </div>

          {preview ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Tile label="Would zero" value={preview.scanned ?? 0} tone="amber-300" />
              <Tile label="Credits at stake" value={(preview.total_credits_zeroed ?? 0).toLocaleString()} tone="amber-300" />
              <Tile label="Skipped · admins" value={preview.skipped?.admins ?? 0} />
              <Tile label="Skipped · subscribers" value={preview.skipped?.subscribers ?? 0} />
            </div>
          ) : (
            <div className="text-muted font-mono text-sm">Loading…</div>
          )}

          {preview?.sample?.length ? (
            <div className="brutal-card p-0 overflow-hidden">
              <div className="px-4 py-3 border-b border-violet-500/20 text-xs font-mono uppercase tracking-widest text-muted">
                Sample (first 20 affected users)
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-violet-500/5 text-[10px] uppercase tracking-widest text-muted font-mono">
                    <tr>
                      <th className="px-4 py-2 text-left">Email</th>
                      <th className="px-4 py-2 text-left">Plan</th>
                      <th className="px-4 py-2 text-left">Status</th>
                      <th className="px-4 py-2 text-right">Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((u) => (
                      <tr key={u.user_id} className="border-t border-violet-500/10">
                        <td className="px-4 py-2 text-ink font-mono text-xs">{u.email || u.user_id}</td>
                        <td className="px-4 py-2 text-muted">{u.plan_id || "free"}</td>
                        <td className="px-4 py-2 text-muted">{u.plan_status || "—"}</td>
                        <td className="px-4 py-2 text-right text-amber-300 font-mono">{u.balance_before}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            preview && (
              <div className="brutal-card border-emerald/40 bg-emerald-500/10 p-4 text-sm text-emerald-200" data-testid="admin-enforce-clean">
                ✓ Zero stray balances detected. The 0-credit policy is fully enforced.
              </div>
            )
          )}
        </section>

        {lastRun && (
          <section className="brutal-card border-emerald/40 bg-emerald-500/5 p-5 space-y-2" data-testid="admin-enforce-last-run">
            <div className="text-emerald-300 font-mono text-xs uppercase tracking-widest">Last run</div>
            <div className="text-ink">
              Zeroed <span className="font-bold text-emerald-200">{lastRun.affected}</span> account{lastRun.affected === 1 ? "" : "s"} (
              <span className="font-bold text-emerald-200">{lastRun.total_credits_zeroed?.toLocaleString()}</span> credits).
              Skipped {lastRun.skipped?.admins ?? 0} admins · {lastRun.skipped?.subscribers ?? 0} subscribers.
            </div>
          </section>
        )}

        <section className="brutal-card border-amber/40 bg-amber-500/5 p-5 space-y-3">
          <h2 className="font-display text-lg text-ink">Run for real</h2>
          <p className="text-sm text-muted">
            This will set <code className="text-xs">credits_balance = 0</code> for every non-admin, non-subscriber user
            with a positive balance and write an audit row per user. Active subscribers and admin-unlimited accounts
            are never affected. The sweep is idempotent — re-running on a clean slate is a no-op.
          </p>
          <button
            type="button"
            onClick={() => setConfirmOpen(true)}
            disabled={busy || !preview || preview.scanned === 0}
            className="brutal-btn bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-40"
            data-testid="admin-enforce-open-confirm"
          >
            {preview?.scanned === 0 ? "Nothing to do" : `Run sweep · zero ${preview?.scanned ?? 0} accounts`}
          </button>
        </section>
      </div>

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80" data-testid="admin-enforce-confirm-modal">
          <div className="brutal-card max-w-md w-full p-6 space-y-4 bg-stone-950">
            <h3 className="font-display text-xl text-ink">Zero {preview?.scanned ?? 0} balances?</h3>
            <p className="text-sm text-muted">
              Type <code className="text-amber-300">enforce zero policy</code> to confirm. This action is
              recorded in the audit ledger and cannot be undone via the dashboard (you&apos;d have to manually
              credit individual users back).
            </p>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="enforce zero policy"
              className="brutal-input w-full"
              data-testid="admin-enforce-confirm-input"
            />
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => { setConfirmOpen(false); setConfirmText(""); }}
                className="text-sm text-muted hover:text-ink"
                data-testid="admin-enforce-cancel"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={runForReal}
                disabled={busy || confirmText.trim().toLowerCase() !== "enforce zero policy"}
                className="brutal-btn bg-rose-500 text-white hover:bg-rose-400 disabled:opacity-40"
                data-testid="admin-enforce-confirm-run"
              >
                {busy ? "Running…" : "Run sweep"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
