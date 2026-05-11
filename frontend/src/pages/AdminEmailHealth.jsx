/**
 * Admin: Email Health
 *
 * Read-only diagnostics for the multi-provider email send pipeline.
 *  - Configured provider chain (env-driven)
 *  - 24h totals + per-provider success/failure/latency
 *  - Recent send attempts (last 50) with provider, purpose, status, error code
 *
 * No raw recipient addresses are surfaced — only the email domain.
 * Refresh-on-mount; admins can hit the "Refresh" button manually.
 */
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function PctPill({ value }) {
  if (value == null) return <span className="font-mono text-xs text-muted">—</span>;
  const pct = Math.round(value * 1000) / 10;
  const tone =
    pct >= 99 ? "tag-emerald" : pct >= 95 ? "tag-amber" : "tag-rose";
  return (
    <span className={`tag ${tone}`} data-testid="email-success-pill">{pct}%</span>
  );
}

function ConfigRow({ name, cfg }) {
  const isResend = name === "resend";
  return (
    <div className="brutal-card p-4 flex flex-col gap-1.5" data-testid={`email-cfg-${name}`}>
      <div className="flex items-center justify-between">
        <div className="font-display font-bold text-base capitalize">{name}</div>
        {cfg.configured ? (
          <span className="tag tag-emerald" data-testid={`email-cfg-${name}-status`}>CONFIGURED</span>
        ) : (
          <span className="tag tag-rose" data-testid={`email-cfg-${name}-status`}>MISSING</span>
        )}
      </div>
      <div className="text-[11px] font-mono text-muted">
        {isResend ? (
          <span>from: {cfg.from || "—"}</span>
        ) : (
          <span>
            host: {cfg.host || "—"} · port: {cfg.port} · tls: {cfg.use_tls ? "yes" : "no"} · from: {cfg.from || "—"}
          </span>
        )}
      </div>
    </div>
  );
}

export default function AdminEmailHealth() {
  const { user, loading: authLoading } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/admin/email/health");
      setData(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not load email diagnostics.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (!authLoading && user?.role === "admin") load(); }, [authLoading, user]);

  if (authLoading) return <div className="min-h-screen page-bg"><Navbar /><div className="max-w-5xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>;
  if (!user) return <Navigate to="/login?redirect=/admin/email-health" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="email-health-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This diagnostics surface is for operators.</p>
          </div>
        </div>
      </div>
    );
  }

  const cfg = data?.configured;
  const totals = data?.totals_24h;
  const perProv = data?.per_provider_24h || [];
  const recent = data?.recent || [];

  return (
    <div className="min-h-screen page-bg" data-testid="admin-email-health-page">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">OPERATIONS · EMAIL HEALTH</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Email pipeline.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Multi-provider send chain. The system tries each provider in order; the first ok wins.
            Failures cascade silently — users never see provider state.
          </p>
          <div className="flex items-center gap-2 pt-2">
            <button onClick={load} className="btn-ghost text-xs" data-testid="email-health-refresh">
              {loading ? "Loading…" : "Refresh"}
            </button>
            {data && <span className="text-[10px] font-mono uppercase tracking-widest text-muted">Provider order: {cfg?.order?.join(" → ")}</span>}
          </div>
          {error && (
            <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="email-health-error">{error}</div>
          )}
        </header>

        {data && (
          <>
            <section className="space-y-3" data-testid="email-health-config-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Configuration</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <ConfigRow name="resend" cfg={cfg.resend} />
                <ConfigRow name="smtp" cfg={cfg.smtp} />
              </div>
            </section>

            <section className="space-y-3" data-testid="email-health-totals-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Last 24 hours</h2>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="brutal-card p-4">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Attempts</div>
                  <div className="text-2xl font-display font-bold" data-testid="email-totals-attempts">{totals?.total ?? 0}</div>
                </div>
                <div className="brutal-card p-4">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Successful</div>
                  <div className="text-2xl font-display font-bold text-emerald-300" data-testid="email-totals-ok">{totals?.ok ?? 0}</div>
                </div>
                <div className="brutal-card p-4">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Failed</div>
                  <div className="text-2xl font-display font-bold text-rose-300" data-testid="email-totals-failed">{totals?.failures ?? 0}</div>
                </div>
                <div className="brutal-card p-4">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Success rate</div>
                  <div className="text-2xl font-display font-bold" data-testid="email-totals-rate"><PctPill value={totals?.success_rate} /></div>
                </div>
              </div>
            </section>

            <section className="space-y-3" data-testid="email-health-per-provider-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Per provider · 24h</h2>
              {perProv.length === 0 ? (
                <div className="text-sm text-muted font-mono" data-testid="email-per-provider-empty">No send attempts in the last 24h.</div>
              ) : (
                <div className="brutal-card overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                        <th className="p-3">Provider</th>
                        <th className="p-3">Attempts</th>
                        <th className="p-3">Success</th>
                        <th className="p-3">Failed</th>
                        <th className="p-3">Rate</th>
                        <th className="p-3">Avg latency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {perProv.map((p) => (
                        <tr key={p.provider} className="border-t border-white/5" data-testid={`email-per-provider-${p.provider}`}>
                          <td className="p-3 font-mono capitalize">{p.provider}</td>
                          <td className="p-3 font-mono">{p.total}</td>
                          <td className="p-3 font-mono text-emerald-300">{p.ok}</td>
                          <td className="p-3 font-mono text-rose-300">{p.failures}</td>
                          <td className="p-3"><PctPill value={p.success_rate} /></td>
                          <td className="p-3 font-mono text-muted">{p.avg_latency_ms} ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="space-y-3" data-testid="email-health-recent-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Recent attempts (last 50)</h2>
              {recent.length === 0 ? (
                <div className="text-sm text-muted font-mono" data-testid="email-recent-empty">No recent send attempts.</div>
              ) : (
                <div className="brutal-card overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                        <th className="p-3">Time</th>
                        <th className="p-3">Group</th>
                        <th className="p-3">Provider</th>
                        <th className="p-3">Purpose</th>
                        <th className="p-3">To · domain</th>
                        <th className="p-3">Status</th>
                        <th className="p-3">Error</th>
                        <th className="p-3">Latency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.map((e) => (
                        <tr key={e.event_id} className="border-t border-white/5" data-testid={`email-recent-${e.event_id}`}>
                          <td className="p-3 font-mono whitespace-nowrap">{e.timestamp?.slice(11, 19)}</td>
                          <td className="p-3 font-mono text-muted">{e.event_group}</td>
                          <td className="p-3 font-mono capitalize">{e.provider}</td>
                          <td className="p-3 font-mono">{e.purpose}</td>
                          <td className="p-3 font-mono text-muted">{e.recipient_domain || "—"}</td>
                          <td className="p-3">
                            {e.ok ? <span className="tag tag-emerald">OK</span> : <span className="tag tag-rose">FAIL</span>}
                          </td>
                          <td className="p-3 font-mono text-muted">{e.error_code || "—"}</td>
                          <td className="p-3 font-mono text-muted">{e.latency_ms} ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <footer className="text-[11px] font-mono uppercase tracking-widest text-muted pt-6 border-t border-white/5" data-testid="email-health-footer">
              Resend → SMTP failover · 24h rolling window · Recipient addresses not surfaced
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
