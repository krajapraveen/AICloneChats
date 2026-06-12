/**
 * Admin: Renewal Reminders
 *
 * Visibility into the renewal-reminder pipeline:
 *   - Today: due / sent / failed / skipped
 *   - Next 50 expiring subscriptions with a per-row "already reminded?" flag
 *   - Recent run history (last 10 cron-style entries)
 *   - One-click manual trigger (with dry-run option)
 *   - Scheduler doc pointer + curl-snippet so an operator can paste the
 *     exact command into their cron the same minute they read it
 */
import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { toast } from "sonner";
import api, { API } from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function formatDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function Tile({ label, value, tone, testId, sub }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className={`text-2xl font-display font-bold mt-0.5 ${tone || ""}`}>{value ?? "—"}</div>
      {sub != null && <div className="text-[11px] font-mono text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

export default function AdminRenewalReminders() {
  const { user, loading: authLoading } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/admin/billing/renewal-reminders/summary");
      setData(data);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load renewal-reminder summary.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!authLoading && user?.role === "admin") {
      Promise.resolve().then(load);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user]);

  const triggerRun = async (dryRun) => {
    setRunning(true);
    try {
      const { data } = await api.post(`/admin/billing/run-renewal-reminders?dry_run=${dryRun ? "true" : "false"}`);
      toast.success(
        `${dryRun ? "Dry run" : "Run"}: examined ${data.examined}, sent ${data.sent}, ` +
        `failures ${data.failures}, skipped (already) ${data.skipped_already}`
      );
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail?.message || "Run failed.");
    } finally {
      setRunning(false);
    }
  };

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/renewal-reminders" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="rr-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  const today = data?.today;
  const upcoming = data?.next_expiring || [];
  const recent = data?.recent_runs || [];

  const curlSnippet = `curl -X POST \\
  -H "Authorization: Bearer <ADMIN_TOKEN>" \\
  -H "Content-Type: application/json" \\
  ${API}/admin/billing/run-renewal-reminders`;

  return (
    <div className="min-h-screen page-bg" data-testid="admin-renewal-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">REVENUE · RENEWAL REMINDERS</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Renewal reminders.</h1>
          <p className="text-sm text-muted max-w-2xl">
            One-shot email 3 days before expiry. Idempotent per <code className="font-mono text-amber/80">order_id</code> —
            safe to run hundreds of times daily. Recommended schedule: <strong>09:00 UTC</strong> daily via Cloudflare Cron or GitHub Actions.
          </p>
          <div className="flex items-center gap-2 pt-2 flex-wrap">
            <button onClick={load} className="btn-ghost text-xs" disabled={loading} data-testid="rr-refresh">
              {loading ? "Loading…" : "Refresh"}
            </button>
            <button onClick={() => triggerRun(true)} className="btn-ghost text-xs" disabled={running} data-testid="rr-dryrun-btn">
              {running ? "Running…" : "Dry-run"}
            </button>
            <button onClick={() => triggerRun(false)} className="btn-brutal text-xs" disabled={running} data-testid="rr-run-btn">
              {running ? "Running…" : "Run now"}
            </button>
          </div>
          {error && (
            <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="rr-error">{error}</div>
          )}
        </header>

        {data && (
          <>
            {/* Today's tiles */}
            <section className="space-y-3" data-testid="rr-today-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Today</h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <Tile label="Due today" value={today?.due} tone="text-amber" testId="rr-tile-due" />
                <Tile label="Sent today" value={today?.sent} tone="text-emerald-300" testId="rr-tile-sent" />
                <Tile label="Failed today" value={today?.failed} tone="text-rose-soft" testId="rr-tile-failed" />
                <Tile
                  label="Skipped (already)"
                  value={today?.skipped_already_reminded}
                  testId="rr-tile-skipped-already"
                  sub="Cycle deduped"
                />
                <Tile
                  label="Skipped (admin)"
                  value={today?.skipped_admin}
                  testId="rr-tile-skipped-admin"
                  sub="Unlimited tier"
                />
                <Tile label="Runs today" value={today?.runs} testId="rr-tile-runs" />
              </div>
            </section>

            {/* Next expiring */}
            <section className="space-y-3" data-testid="rr-upcoming-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
                Next expiring subscriptions ({upcoming.length})
              </h2>
              {upcoming.length === 0 ? (
                <div className="brutal-card p-6 text-sm text-muted font-mono" data-testid="rr-upcoming-empty">
                  No subscriptions expire in the next {data?.config?.reminder_window_days} days.
                </div>
              ) : (
                <div className="brutal-card overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                        <th className="p-3">Days left</th>
                        <th className="p-3">User</th>
                        <th className="p-3">Plan</th>
                        <th className="p-3">Expires at</th>
                        <th className="p-3">Cycle (order)</th>
                        <th className="p-3">Reminded?</th>
                      </tr>
                    </thead>
                    <tbody>
                      {upcoming.map((u) => (
                        <tr key={u.order_id} className="border-t border-white/5" data-testid={`rr-upcoming-${u.order_id}`}>
                          <td className="p-3 font-mono font-bold text-amber">{u.days_left}d</td>
                          <td className="p-3 font-mono text-xs">
                            <div className="text-ink">{u.email}</div>
                            <div className="text-muted">{u.user_id}</div>
                          </td>
                          <td className="p-3 font-mono">{u.plan_id}</td>
                          <td className="p-3 font-mono text-xs text-muted whitespace-nowrap">{formatDateTime(u.expires_at)}</td>
                          <td className="p-3 font-mono text-[11px] text-muted">{u.order_id}</td>
                          <td className="p-3">
                            {u.already_sent
                              ? <span className="tag tag-emerald">SENT</span>
                              : <span className="tag tag-amber">PENDING</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Recent runs */}
            <section className="space-y-3" data-testid="rr-runs-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Recent runs ({recent.length})</h2>
              {recent.length === 0 ? (
                <div className="brutal-card p-6 text-sm text-muted font-mono" data-testid="rr-runs-empty">
                  No runs persisted yet. Hit <strong>Run now</strong> above to bootstrap.
                </div>
              ) : (
                <div className="brutal-card overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                        <th className="p-3">When</th>
                        <th className="p-3">Triggered by</th>
                        <th className="p-3">Examined</th>
                        <th className="p-3">Sent</th>
                        <th className="p-3">Failures</th>
                        <th className="p-3">Skipped</th>
                        <th className="p-3">Dry-run?</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.map((r) => (
                        <tr key={r.run_id} className="border-t border-white/5" data-testid={`rr-run-${r.run_id}`}>
                          <td className="p-3 font-mono text-xs text-muted whitespace-nowrap">{formatDateTime(r.ran_at)}</td>
                          <td className="p-3 font-mono text-xs">{r.triggered_by || "—"}</td>
                          <td className="p-3 font-mono">{r.examined}</td>
                          <td className="p-3 font-mono text-emerald-300">{r.sent}</td>
                          <td className="p-3 font-mono text-rose-soft">{r.failures}</td>
                          <td className="p-3 font-mono text-muted">{(r.skipped_admin || 0) + (r.skipped_already || 0)}</td>
                          <td className="p-3 font-mono text-[11px]">{r.dry_run ? "yes" : "no"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Scheduler quick-start */}
            <section className="space-y-3" data-testid="rr-scheduler-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Scheduler setup</h2>
              <div className="brutal-card p-4 space-y-3">
                <p className="text-xs text-muted">
                  Recommended schedule: <strong>{data?.config?.recommended_schedule}</strong>.
                  See <code className="font-mono text-amber/80">{data?.config?.scheduler_doc}</code> for
                  Cloudflare Cron, GitHub Actions, and systemd timer recipes.
                </p>
                <pre className="text-[11px] font-mono bg-black/30 border border-white/10 rounded-lg p-3 overflow-x-auto whitespace-pre" data-testid="rr-curl-snippet">
{curlSnippet}
                </pre>
                <p className="text-[11px] text-muted">
                  Hit the endpoint with any admin bearer token. Response JSON is the same as the "Run now" button above.
                </p>
              </div>
            </section>
          </>
        )}

        <footer className="text-[11px] font-mono uppercase tracking-widest text-muted pt-6 border-t border-white/5" data-testid="rr-footer">
          Idempotent · Dedup by order_id · Skips Admin·Unlimited · Persistent run-log audit
        </footer>
      </div>
    </div>
  );
}
