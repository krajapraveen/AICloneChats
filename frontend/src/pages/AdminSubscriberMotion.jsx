/**
 * Admin: Subscriber Motion + Churn Velocity
 *
 * The headline subscription-business read. Everything here is derived from
 * IMMUTABLE source rows (payment_orders + payment_refunds), so two analysts
 * loading this page at the same instant against the same database see
 * identical numbers.
 *
 * Sections
 * --------
 *   1. Executive Summary       — active subs (start vs end), MRR, ARPPU
 *   2. Subscriber Motion       — new / renewal / won-back / churn breakdown
 *   3. Churn Velocity          — four single-number ratios
 *   4. Trend Charts            — Subscribers · Churn · Revenue (Recharts)
 *
 * Window selector: 7 / 30 / 90 days. Refresh button.
 */
import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import {
  ResponsiveContainer, LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";

const WINDOW_OPTIONS = [7, 30, 90];

function formatInr(amount) {
  if (amount == null) return "—";
  return `₹${Number(amount).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function Tile({ label, value, sub, tone, testId }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className={`text-2xl font-display font-bold mt-0.5 ${tone || ""}`}>{value ?? "—"}</div>
      {sub != null && <div className="text-[11px] font-mono text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function MotionTile({ label, value, kind, testId }) {
  const tone =
    kind === "positive" ? "text-emerald-300" :
    kind === "negative" ? "text-rose-soft" :
    kind === "neutral" ? "text-amber" : "";
  return <Tile label={label} value={value} tone={tone} testId={testId} />;
}

const CHART_TOOLTIP_STYLE = {
  contentStyle: { background: "rgba(13,13,16,0.95)", border: "1px solid rgba(255,255,255,0.12)", fontSize: 12 },
  labelStyle: { color: "#aaa", fontSize: 11 },
  itemStyle: { color: "#fff", fontSize: 11 },
};

function ChartShell({ title, children, testId }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-3">{title}</div>
      <div style={{ width: "100%", height: 220 }}>
        {children}
      </div>
    </div>
  );
}

export default function AdminSubscriberMotion() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(30);
  const [motion, setMotion] = useState(null);
  const [trend, setTrend] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, t] = await Promise.all([
        api.get(`/admin/revenue/subscriber-motion?days=${days}`),
        api.get(`/admin/revenue/subscriber-trend?days=${days}`),
      ]);
      setMotion(m.data);
      setTrend(t.data);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load subscriber motion.");
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

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/subscriber-motion" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="motion-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  const m = motion?.motion;
  const v = motion?.velocity;
  const es = motion?.executive_summary;

  const chartPoints = (trend?.points || []).map((p) => ({
    ...p,
    // Friendly label for the X axis
    label: p.t ? new Date(p.t).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "",
  }));

  return (
    <div className="min-h-screen page-bg" data-testid="admin-motion-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">ANALYTICS · SUBSCRIBER MOTION</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Subscriber motion · churn velocity.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Net subscriber movement and the four velocity ratios that matter.
            Every number is computed from immutable payment + refund rows —
            never from current-state snapshots — so the math is reproducible.
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
                  data-testid={`motion-window-${d}`}
                >
                  {d}d
                </button>
              ))}
            </div>
            <button onClick={load} className="btn-ghost text-xs" disabled={loading} data-testid="motion-refresh">
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
          {error && (
            <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="motion-error">{error}</div>
          )}
        </header>

        {motion && (
          <>
            {/* ── 1. Executive Summary ──────────────────────── */}
            <section className="space-y-3" data-testid="motion-exec-summary">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Executive summary · {days}d</h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <Tile label="Active · start" value={es?.active_subscribers_start} testId="es-active-start" />
                <Tile label="Active · end" value={es?.active_subscribers_end} testId="es-active-end" />
                <Tile
                  label="Net growth"
                  value={`${es?.net_growth_pct >= 0 ? "+" : ""}${es?.net_growth_pct}%`}
                  tone={(es?.net_growth_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-soft"}
                  testId="es-net-growth"
                />
                <Tile label={`Revenue · ${days}d`} value={formatInr(es?.window_revenue_inr)} tone="text-emerald-300" testId="es-revenue" />
                <Tile label="MRR estimate" value={formatInr(es?.mrr_estimate_inr)} testId="es-mrr" />
                <Tile label="ARPPU" value={formatInr(es?.arppu_inr)} testId="es-arppu" />
              </div>
              <p className="text-[10px] font-mono text-muted">
                MRR = (window revenue / window days) × 30 · ARPPU = MRR / active subscribers at end
              </p>
            </section>

            {/* ── 2. Subscriber Motion ──────────────────────── */}
            <section className="space-y-3" data-testid="motion-counts">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Subscriber motion</h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <MotionTile label="New subscribers" value={m?.new_subscribers} kind="positive" testId="motion-new" />
                <MotionTile label="Renewals" value={m?.renewals} kind="positive" testId="motion-renewals" />
                <MotionTile label="Won-back" value={m?.won_back} kind="positive" testId="motion-wonback" />
                <MotionTile label="Cancel churn" value={m?.cancel_churn} kind="negative" testId="motion-cancel-churn" />
                <MotionTile label="Expire churn" value={m?.expire_churn} kind="negative" testId="motion-expire-churn" />
                <MotionTile label="Refund churn" value={m?.refund_churn} kind="negative" testId="motion-refund-churn" />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <MotionTile label="Total churn (cancel + expire + refund)" value={m?.total_churn} kind="negative" testId="motion-total-churn" />
                <MotionTile
                  label="Net subscriber change"
                  value={`${m?.net_subscriber_change >= 0 ? "+" : ""}${m?.net_subscriber_change}`}
                  kind={m?.net_subscriber_change >= 0 ? "positive" : "negative"}
                  testId="motion-net-change"
                />
              </div>
            </section>

            {/* ── 3. Churn Velocity ─────────────────────────── */}
            <section className="space-y-3" data-testid="motion-velocity">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Churn velocity</h2>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <MotionTile label="Churn rate" value={`${v?.churn_rate_pct}%`} kind="negative" testId="velocity-churn" />
                <MotionTile label="Renewal rate" value={`${v?.renewal_rate_pct}%`} kind="positive" testId="velocity-renewal" />
                <MotionTile label="Won-back rate" value={`${v?.wonback_rate_pct}%`} kind="positive" testId="velocity-wonback" />
                <MotionTile
                  label="Net growth"
                  value={`${v?.net_growth_pct >= 0 ? "+" : ""}${v?.net_growth_pct}%`}
                  kind={v?.net_growth_pct >= 0 ? "positive" : "negative"}
                  testId="velocity-net-growth"
                />
              </div>
              <p className="text-[10px] font-mono text-muted">
                Denominator = active subscribers at the start of the window (zero-floor to avoid div/0)
              </p>
            </section>

            {/* ── 4. Trend Charts ───────────────────────────── */}
            <section className="space-y-3" data-testid="motion-trends">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Trends · {trend?.bucket_hours}h buckets</h2>
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <ChartShell title="Active subscribers" testId="chart-active">
                  <ResponsiveContainer>
                    <AreaChart data={chartPoints} margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
                      <CartesianGrid stroke="rgba(255,255,255,0.05)" strokeDasharray="3 3" />
                      <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#888" }} interval="preserveStartEnd" />
                      <YAxis tick={{ fontSize: 10, fill: "#888" }} allowDecimals={false} />
                      <Tooltip {...CHART_TOOLTIP_STYLE} />
                      <Area dataKey="active" stroke="#6EE7B7" fill="#6EE7B7" fillOpacity={0.2} strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                </ChartShell>

                <ChartShell title="Churn vs additions" testId="chart-churn">
                  <ResponsiveContainer>
                    <BarChart data={chartPoints} margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
                      <CartesianGrid stroke="rgba(255,255,255,0.05)" strokeDasharray="3 3" />
                      <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#888" }} interval="preserveStartEnd" />
                      <YAxis tick={{ fontSize: 10, fill: "#888" }} allowDecimals={false} />
                      <Tooltip {...CHART_TOOLTIP_STYLE} />
                      <Bar dataKey="new_subscribers" stackId="add" fill="#6EE7B7" />
                      <Bar dataKey="renewals" stackId="add" fill="#A78BFA" />
                      <Bar dataKey="won_back" stackId="add" fill="#FCD34D" />
                      <Bar dataKey="churn" fill="#FDA4AF" />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartShell>

                <ChartShell title="Revenue" testId="chart-revenue">
                  <ResponsiveContainer>
                    <LineChart data={chartPoints} margin={{ top: 4, right: 8, left: -8, bottom: 4 }}>
                      <CartesianGrid stroke="rgba(255,255,255,0.05)" strokeDasharray="3 3" />
                      <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#888" }} interval="preserveStartEnd" />
                      <YAxis tick={{ fontSize: 10, fill: "#888" }} tickFormatter={(v) => `₹${v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}`} />
                      <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(val) => [formatInr(val), "Revenue"]} />
                      <Line dataKey="revenue_inr" stroke="#FCD34D" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </ChartShell>
              </div>
            </section>

            {/* ── Definitions ───────────────────────────────── */}
            <section className="space-y-3" data-testid="motion-definitions">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Definitions</h2>
              <div className="brutal-card p-4">
                <dl className="text-xs space-y-2">
                  {motion.definitions && Object.entries(motion.definitions).map(([k, v]) => (
                    <div key={k} className="grid grid-cols-1 sm:grid-cols-[180px_1fr] gap-2">
                      <dt className="font-mono uppercase tracking-wider text-amber/80">{k}</dt>
                      <dd className="text-muted">{v}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </section>
          </>
        )}

        <footer className="text-[11px] font-mono uppercase tracking-widest text-muted pt-6 border-t border-white/5" data-testid="motion-footer">
          Derived from payment_orders + payment_refunds · No current-state reads · Two reads return identical numbers
        </footer>
      </div>
    </div>
  );
}
