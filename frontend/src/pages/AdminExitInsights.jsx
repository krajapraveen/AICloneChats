/**
 * Admin: Exit Insights
 *
 * Surfaces *why* users leave — combining the two existing exit signals:
 *  1. Account deletions   (account_deletion_events.reason)
 *  2. Subscription cancels (users.cancel_reason)
 *
 * "The system remembers; it does not chase." This dashboard is observation-
 * only: there is no retention CTA, no auto-email, no popup. We surface the
 * trend so operators can make product decisions. Acting on individual users
 * would violate the no-chasing philosophy.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";

const WINDOW_OPTIONS = [30, 90, 180];

const BUCKET_LABEL = {
  pricing: "Pricing",
  missing_feature: "Missing feature",
  quality: "Quality / accuracy",
  ux: "UX / bugs",
  privacy: "Privacy concerns",
  not_using: "Not using",
  alternative: "Switched to alternative",
  trust: "Trust / discomfort",
  other: "Other (free-form)",
  no_reason: "No reason given",
};

const BUCKET_TONE = {
  pricing: "text-amber",
  missing_feature: "text-violet-soft",
  quality: "text-rose-soft",
  ux: "text-sky-300",
  privacy: "text-emerald-300",
  not_using: "text-muted",
  alternative: "text-rose-soft",
  trust: "text-amber",
  other: "text-ink/80",
  no_reason: "text-muted",
};

const CHART_TOOLTIP_STYLE = {
  contentStyle: { background: "rgba(13,13,16,0.95)", border: "1px solid rgba(255,255,255,0.12)", fontSize: 12 },
  itemStyle: { color: "#e5e7eb", fontSize: 12 },
  labelStyle: { color: "rgba(255,255,255,0.5)", fontSize: 11, fontFamily: "monospace" },
};

function Tile({ label, value, sub, tone, testId }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className={`text-2xl font-display font-bold mt-0.5 ${tone || ""}`}>{value ?? "—"}</div>
      {sub != null && <div className="text-[11px] font-mono text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function formatAt(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

export default function AdminExitInsights() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(90);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get(`/admin/exit-insights?days=${days}`);
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load exit insights.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!authLoading && user?.role === "admin") {
      Promise.resolve().then(load);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user, days]);

  const bucketChartData = useMemo(() => {
    if (!data?.by_reason_bucket) return [];
    return data.by_reason_bucket
      .filter((b) => b.bucket !== "no_reason") // exclude blank from the "why" view
      .map((b) => ({ label: BUCKET_LABEL[b.bucket] || b.bucket, count: b.count, bucket: b.bucket }));
  }, [data]);

  const monthlyChartData = useMemo(() => {
    if (!data?.monthly_series) return [];
    return data.monthly_series.map((m) => ({
      ...m,
      label: m.month,  // already in YYYY-MM
    }));
  }, [data]);

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/exit-insights" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="ei-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  const s = data?.summary;

  return (
    <div className="min-h-screen page-bg" data-testid="admin-exit-insights-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">ANALYTICS · EXIT INSIGHTS</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Why users leave.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Account deletions + pending subscription cancellations, bucketed by
            free-form reason. <span className="text-amber/80">The system remembers; it does not chase.</span>{" "}
            This view exists so operators can act on product, not to trigger retention nags.
          </p>
          <div className="flex items-center gap-3 pt-2 flex-wrap">
            <div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-muted">
              <span>Window:</span>
              {WINDOW_OPTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDays(d)}
                  className={`px-2.5 py-1 rounded-md border text-xs font-mono ${
                    days === d ? "bg-amber/20 border-amber/50 text-amber" : "bg-white/[0.02] border-white/10 text-ink/70 hover:bg-white/[0.06]"
                  }`}
                  data-testid={`ei-window-${d}`}
                >
                  {d}d
                </button>
              ))}
            </div>
            <button onClick={load} className="btn-ghost text-xs" disabled={loading} data-testid="ei-refresh">
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
          {error && (
            <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="ei-error">{error}</div>
          )}
        </header>

        {s && (
          <>
            <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3" data-testid="ei-summary-tiles">
              <Tile label="Total exits" value={s.total_exits} testId="ei-tile-total" />
              <Tile label="Account deletions" value={s.deletions} tone="text-rose-soft" testId="ei-tile-deletions" />
              <Tile label="Sub cancellations" value={s.subscription_cancellations} tone="text-amber" testId="ei-tile-cancels" />
              <Tile label="With reason" value={s.exits_with_reason} tone="text-emerald-300" testId="ei-tile-with-reason" />
              <Tile
                label="Reason capture rate"
                value={`${s.reason_capture_rate_pct}%`}
                tone={s.reason_capture_rate_pct >= 50 ? "text-emerald-300" : "text-amber"}
                sub={s.exits_with_reason + "/" + s.total_exits}
                testId="ei-tile-capture"
              />
            </section>

            {/* Bucket distribution */}
            <section className="space-y-3" data-testid="ei-bucket-chart-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
                Reason distribution · {days}d
              </h2>
              {bucketChartData.length === 0 ? (
                <div className="brutal-card p-6 text-sm text-muted" data-testid="ei-bucket-empty">
                  No reasons captured in this window.
                </div>
              ) : (
                <div className="brutal-card p-3" data-testid="ei-bucket-chart">
                  <div style={{ width: "100%", height: 260 }}>
                    <ResponsiveContainer>
                      <BarChart data={bucketChartData} layout="vertical" margin={{ top: 5, right: 20, left: 110, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis type="number" stroke="rgba(255,255,255,0.5)" fontSize={11} tickLine={false} />
                        <YAxis dataKey="label" type="category" stroke="rgba(255,255,255,0.5)" fontSize={11}
                          width={110} tickLine={false} />
                        <Tooltip {...CHART_TOOLTIP_STYLE} />
                        <Bar dataKey="count" fill="#a78bfa" radius={[0, 3, 3, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Bucket cards with sample reasons */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="ei-bucket-cards">
                {data.by_reason_bucket.map((b) => (
                  <div key={b.bucket} className="brutal-card p-4" data-testid={`ei-bucket-${b.bucket}`}>
                    <div className="flex items-center justify-between">
                      <div className={`text-xs font-mono uppercase tracking-widest ${BUCKET_TONE[b.bucket] || "text-ink"}`}>
                        {BUCKET_LABEL[b.bucket] || b.bucket}
                      </div>
                      <div className="text-xl font-display font-bold">{b.count}</div>
                    </div>
                    {b.examples?.length > 0 && (
                      <ul className="mt-2 space-y-1 text-[11px] text-ink/80">
                        {b.examples.map((ex, i) => (
                          <li key={i} className="line-clamp-2" title={ex}>· {ex}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* Monthly trend */}
            <section className="space-y-3" data-testid="ei-monthly-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
                Exits per month · deletions vs cancellations
              </h2>
              {monthlyChartData.length === 0 ? (
                <div className="brutal-card p-6 text-sm text-muted" data-testid="ei-monthly-empty">
                  No exits in this window.
                </div>
              ) : (
                <div className="brutal-card p-3" data-testid="ei-monthly-chart">
                  <div style={{ width: "100%", height: 240 }}>
                    <ResponsiveContainer>
                      <BarChart data={monthlyChartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis dataKey="label" stroke="rgba(255,255,255,0.5)" fontSize={11} tickLine={false} />
                        <YAxis stroke="rgba(255,255,255,0.5)" fontSize={11} tickLine={false} width={40} />
                        <Tooltip {...CHART_TOOLTIP_STYLE} />
                        <Legend wrapperStyle={{ fontSize: 11, fontFamily: "monospace" }} />
                        <Bar dataKey="deletions" name="Deletions" stackId="x" fill="#fb7185" />
                        <Bar dataKey="cancellations" name="Cancellations" stackId="x" fill="#fbbf24" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}
            </section>

            {/* Recent exits feed */}
            <section className="space-y-3" data-testid="ei-recent-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
                Recent exits · most recent first
              </h2>
              <div className="brutal-card overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                      <th className="p-3">When</th>
                      <th className="p-3">Kind</th>
                      <th className="p-3">Reason bucket</th>
                      <th className="p-3">Free-form reason</th>
                      <th className="p-3">Auth / plan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_exits.map((e, i) => (
                      <tr key={`${e.user_id}-${i}`} className="border-t border-white/5"
                        data-testid={`ei-recent-row-${i}`}>
                        <td className="p-3 font-mono text-[11px] text-muted whitespace-nowrap">{formatAt(e.at)}</td>
                        <td className="p-3 font-mono text-[11px]">
                          <span className={e.kind === "deletion" ? "text-rose-soft" : "text-amber"}>
                            {e.kind}
                          </span>
                        </td>
                        <td className={`p-3 font-mono text-[11px] ${BUCKET_TONE[e.bucket] || ""}`}>
                          {BUCKET_LABEL[e.bucket] || e.bucket}
                        </td>
                        <td className="p-3 text-[12px] text-ink/90 max-w-md line-clamp-2" title={e.reason || ""}>
                          {e.reason || <span className="text-muted">—</span>}
                        </td>
                        <td className="p-3 font-mono text-[11px] text-muted whitespace-nowrap">
                          {e.auth_provider || e.plan_id || "—"}
                        </td>
                      </tr>
                    ))}
                    {data.recent_exits.length === 0 && (
                      <tr>
                        <td colSpan={5} className="p-6 text-center text-muted text-sm">
                          No recent exits in this window.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
