/**
 * Anonymous Reality — Observability Dashboard (read-only).
 *
 * Operator constraint: this is INSTRUMENTATION, not product expansion.
 * No CRUD. No moderation actions (those live at /admin/anonymous-reality).
 * Auto-refreshes every 45s so the freeze decision is based on fresh data.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

const REFRESH_MS = 45_000;

function pct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${Number(n).toFixed(1)}%`;
}

function num(n) {
  if (n === null || n === undefined) return "—";
  if (typeof n !== "number") return String(n);
  if (n >= 1000) return n.toLocaleString();
  return String(n);
}

function fmtDuration(sec) {
  if (!sec || sec < 0) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ${Math.round(sec % 60)}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function StatCard({ label, value, sub, testid, tone = "default" }) {
  const toneClass =
    tone === "good" ? "border-emerald" :
    tone === "bad" ? "border-rose" :
    tone === "warn" ? "border-amber" : "";
  return (
    <div className={`brutal-card p-4 sm:p-5 ${toneClass}`} data-testid={testid}>
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display font-black text-2xl sm:text-3xl mt-1 text-ink break-words">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

function Sparkline({ series }) {
  // series: [{day, users}]
  const data = Array.isArray(series) ? series.slice(-14) : [];
  if (!data.length) return <div className="text-xs text-muted">no activity yet</div>;
  const max = Math.max(1, ...data.map((d) => d.users || 0));
  return (
    <div className="flex items-end gap-1 h-16" data-testid="dau-sparkline">
      {data.map((d, i) => {
        const h = Math.max(2, Math.round(((d.users || 0) / max) * 56));
        return (
          <div
            key={i}
            title={`${d.day}: ${d.users} users`}
            className="bg-violet-soft/70 hover:bg-violet-soft transition-colors w-3 sm:w-4 rounded-sm"
            style={{ height: `${h}px` }}
          />
        );
      })}
    </div>
  );
}

function SectionTitle({ children, testid }) {
  return (
    <div className="flex items-baseline justify-between mt-8 mb-3">
      <h2 className="text-base sm:text-lg font-mono uppercase tracking-widest text-ink/85" data-testid={testid}>{children}</h2>
    </div>
  );
}

export default function AdminAnonymousMetrics() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [err, setErr] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastFetchedAt, setLastFetchedAt] = useState(null);
  const timerRef = useRef(null);

  const fetchOnce = useCallback(async () => {
    try {
      const r = await api.get(`/admin/anonymous/observability?days=${days}`);
      setData(r.data);
      setForbidden(false);
      setErr("");
      setLastFetchedAt(new Date().toISOString());
    } catch (e) {
      if (e?.response?.status === 403) { setForbidden(true); }
      else { setErr(e?.response?.data?.detail || "Could not load metrics."); }
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/anonymous-metrics"); return; }
    if (!user) return;
    setLoading(true);
    fetchOnce();
  }, [user, authLoading, days, navigate, fetchOnce]);

  useEffect(() => {
    if (!autoRefresh || !user || forbidden) {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      return;
    }
    timerRef.current = setInterval(fetchOnce, REFRESH_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [autoRefresh, fetchOnce, user, forbidden]);

  const rooms = useMemo(() => data?.rooms?.rows || [], [data]);
  const a = data?.audience;
  const e = data?.engagement;
  const s = data?.safety;
  const r = data?.retention;
  const ro = data?.rooms;

  if (authLoading || !user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  if (forbidden) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-10" data-testid="anon-metrics-forbidden">
          <div className="brutal-card p-8 text-center">
            <h1 className="heading-display text-2xl mb-2">Admin only</h1>
            <p className="text-sm text-ink/70">This dashboard is for admin accounts.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="anon-metrics-page">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-2">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Anonymous Reality · Observability</div>
            <h1 className="heading-display text-3xl sm:text-4xl mt-1">Behavioral evidence dashboard</h1>
            <p className="text-sm text-muted mt-2 max-w-2xl">
              Read-only instrumentation during the measurement freeze. Use these signals to decide
              whether to continue, pivot, kill, or expand. <span className="text-ink/80">No product features will be built from this page.</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 7, 14, 30].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                data-testid={`anon-metrics-window-${d}d`}
                className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border transition-colors ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`}
              >
                {d === 1 ? "24h" : `${d}d`}
              </button>
            ))}
            <label className="ml-2 text-xs font-mono uppercase tracking-widest text-muted flex items-center gap-2 cursor-pointer" data-testid="anon-metrics-autorefresh">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(ev) => setAutoRefresh(ev.target.checked)}
                className="accent-violet-soft"
              />
              auto-refresh
            </label>
            <Link
              to="/admin/anonymous-reality"
              data-testid="anon-metrics-moderation-link"
              className="text-xs font-mono uppercase tracking-widest text-ink/70 hover:text-ink underline underline-offset-4 ml-2"
            >
              Moderation →
            </Link>
          </div>
        </div>

        {err && <div className="brutal-card p-4 text-sm text-rose-300 mb-6" data-testid="anon-metrics-error">{err}</div>}

        {loading && !data && (
          <div className="text-muted font-mono text-sm" data-testid="anon-metrics-loading">loading metrics…</div>
        )}

        {data && (
          <>
            {/* AUDIENCE */}
            <SectionTitle testid="anon-metrics-section-audience">Audience</SectionTitle>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <StatCard testid="anon-metric-dau" label="DAU (24h)" value={num(a?.dau)} sub="distinct active sessions" />
              <StatCard testid="anon-metric-wau" label="WAU (7d)" value={num(a?.wau)} sub="distinct active sessions" />
              <StatCard testid="anon-metric-new-sessions" label={`New sessions · ${days}d`} value={num(a?.sessions_created_in_window)} sub={`all-time: ${num(a?.total_sessions_all_time)}`} />
              <StatCard testid="anon-metric-active-now" label="Active right now" value={num(e?.active_now_total)} sub="across all rooms" />
            </div>
            <div className="brutal-card p-4 sm:p-5 mt-4" data-testid="anon-metric-dau-series">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Daily active sessions · last 14d</div>
              <Sparkline series={a?.daily_active_series} />
            </div>

            {/* ENGAGEMENT */}
            <SectionTitle testid="anon-metrics-section-engagement">Engagement</SectionTitle>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <StatCard testid="anon-metric-msgs" label="Messages (allowed)" value={num(e?.messages_user_allowed)} sub={`total user msgs: ${num(e?.messages_user_total)}`} />
              <StatCard testid="anon-metric-talkers" label="Talkers" value={num(e?.talkers)} sub={`avg ${num(e?.avg_msgs_per_talker)} msgs/talker`} />
              <StatCard
                testid="anon-metric-lurker-ratio"
                label="Lurker:Talker"
                value={e?.lurker_talker_ratio === null || e?.lurker_talker_ratio === undefined ? "—" : `${e.lurker_talker_ratio}:1`}
                sub={`${num(e?.lurkers)} lurkers · ${num(e?.talkers)} talkers`}
                tone={e?.lurker_talker_ratio !== null && e?.lurker_talker_ratio !== undefined && e?.lurker_talker_ratio > 5 ? "warn" : "default"}
              />
              <StatCard testid="anon-metric-session-duration" label="Avg session duration" value={fmtDuration(e?.avg_session_duration_sec)} sub="created → last_seen" />
              <StatCard testid="anon-metric-peak" label="Peak concurrent (est.)" value={num(e?.peak_concurrent_estimate)} sub="busiest 10-min bucket" />
            </div>

            {/* SAFETY */}
            <SectionTitle testid="anon-metrics-section-safety">Safety & Moderation</SectionTitle>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <StatCard
                testid="anon-metric-block-rate"
                label="Block rate"
                value={pct(s?.block_rate_pct)}
                sub={`${num(s?.blocked)} blocked of ${num(e?.messages_user_total)}`}
                tone={s?.block_rate_pct >= 25 ? "bad" : s?.block_rate_pct >= 12 ? "warn" : "good"}
              />
              <StatCard
                testid="anon-metric-report-rate"
                label="Report rate"
                value={pct(s?.report_rate_pct)}
                sub={`${num(s?.reports)} reports`}
                tone={s?.report_rate_pct >= 1 ? "bad" : "good"}
              />
              <StatCard
                testid="anon-metric-ai-reply"
                label="AI reply usage"
                value={pct(s?.ai_reply_usage_pct)}
                sub={`${num(s?.system_messages)} system replies`}
              />
              <StatCard testid="anon-metric-escalated" label="Escalated (self-harm)" value={num(s?.escalated)} sub="supportive crisis path" tone={s?.escalated > 0 ? "warn" : "default"} />
            </div>

            {/* RETENTION */}
            <SectionTitle testid="anon-metrics-section-retention">Retention</SectionTitle>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <StatCard
                testid="anon-metric-d1"
                label="D1 retention"
                value={r?.d1_pct === null || r?.d1_pct === undefined ? "—" : pct(r?.d1_pct)}
                sub={`${num(r?.d1_returned)}/${num(r?.d1_eligible)} returned`}
                tone={r?.d1_pct !== null && r?.d1_pct >= 25 ? "good" : r?.d1_pct !== null && r?.d1_pct < 10 ? "bad" : "default"}
              />
              <StatCard
                testid="anon-metric-d7"
                label="D7 retention"
                value={r?.d7_pct === null || r?.d7_pct === undefined ? "—" : pct(r?.d7_pct)}
                sub={`${num(r?.d7_returned)}/${num(r?.d7_eligible)} returned`}
                tone={r?.d7_pct !== null && r?.d7_pct >= 10 ? "good" : r?.d7_pct !== null && r?.d7_pct < 3 ? "bad" : "default"}
              />
              <StatCard
                testid="anon-metric-abandonment"
                label="Room abandonment"
                value={ro?.overall_abandonment_pct === null || ro?.overall_abandonment_pct === undefined ? "—" : pct(ro?.overall_abandonment_pct)}
                sub="joined but never spoke"
                tone={ro?.overall_abandonment_pct >= 80 ? "bad" : ro?.overall_abandonment_pct >= 60 ? "warn" : "good"}
              />
              <StatCard
                testid="anon-metric-room-creation"
                label="User-created rooms"
                value={ro?.user_created_rooms_locked ? "Locked" : num(ro?.rooms_created_in_window)}
                sub={ro?.user_created_rooms_locked ? "Phase 1: seeded only" : `${days}d`}
              />
            </div>

            {/* ROOMS */}
            <SectionTitle testid="anon-metrics-section-rooms">Top active rooms · {days}d window</SectionTitle>
            <div className="brutal-card overflow-x-auto" data-testid="anon-metrics-rooms-table">
              <table className="w-full text-sm">
                <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                  <tr className="border-b border-ink/10">
                    <th className="text-left p-3">Room</th>
                    <th className="text-right p-3">Active now</th>
                    <th className="text-right p-3">Msgs</th>
                    <th className="text-right p-3">Talkers</th>
                    <th className="text-right p-3">Joiners</th>
                    <th className="text-right p-3">Abandon%</th>
                    <th className="text-right p-3">Last msg</th>
                    <th className="text-right p-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rooms.length === 0 && (
                    <tr><td colSpan="8" className="p-6 text-center text-muted">No room activity yet.</td></tr>
                  )}
                  {rooms.map((rm) => (
                    <tr key={rm.slug} className="border-b border-ink/5" data-testid={`anon-metrics-room-row-${rm.slug}`}>
                      <td className="p-3">
                        <div className="font-mono text-xs uppercase tracking-widest text-ink/60">{rm.slug}</div>
                        <div className="text-ink">{rm.title}</div>
                      </td>
                      <td className="p-3 text-right tabular-nums">{num(rm.active_now)}</td>
                      <td className="p-3 text-right tabular-nums">{num(rm.messages)}</td>
                      <td className="p-3 text-right tabular-nums">{num(rm.talkers)}</td>
                      <td className="p-3 text-right tabular-nums">{num(rm.joiners)}</td>
                      <td className="p-3 text-right tabular-nums">{rm.abandonment_pct === null || rm.abandonment_pct === undefined ? "—" : pct(rm.abandonment_pct)}</td>
                      <td className="p-3 text-right text-xs text-muted">{fmtTime(rm.last_message_at)}</td>
                      <td className="p-3 text-right">
                        <span className={`text-[10px] font-mono uppercase tracking-widest px-2 py-0.5 rounded-full ${rm.status === "frozen" ? "bg-rose-500/15 text-rose-300" : "bg-emerald-500/15 text-emerald-soft"}`}>
                          {rm.status || "active"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="text-[11px] font-mono uppercase tracking-widest text-muted mt-6 flex flex-wrap gap-x-4 gap-y-1" data-testid="anon-metrics-footer">
              <span>generated · {fmtTime(data?.generated_at)}</span>
              {lastFetchedAt && <span>last refresh · {fmtTime(lastFetchedAt)}</span>}
              <span>window · {data?.window_days}d</span>
              <span>read-only · no actions on this page</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
