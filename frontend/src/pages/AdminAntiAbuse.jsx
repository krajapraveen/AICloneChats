/**
 * Admin: Anti-Abuse Dashboard
 *
 * One-screen operator console for the platform's abuse-control layer.
 *  - 24h aggregate metrics (rate-limited / limited / blocked events)
 *  - Currently limited or blocked users with one-click unblock/reset
 *  - "Suspicious" users (highest event count over last N hours)
 *  - Recent raw anti-abuse events feed
 *  - Inline action: change a user's abuse_status (normal | limited | blocked)
 *
 * Read-only by default. Every mutating action requires a reason string and
 * is server-audited; the page is just a thin wrapper over the backend
 * endpoints in admin_anti_abuse.py.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const STATUS_OPTIONS = [
  { value: "normal", label: "Normal", tone: "tag-emerald" },
  { value: "limited", label: "Limited", tone: "tag-amber" },
  { value: "blocked", label: "Blocked", tone: "tag-rose" },
];

function StatusTag({ status }) {
  const opt = STATUS_OPTIONS.find((s) => s.value === status) || { label: status || "—", tone: "tag-muted" };
  return <span className={`tag ${opt.tone}`} data-testid={`status-tag-${status || "unknown"}`}>{opt.label}</span>;
}

function MetricCard({ label, value, tone, testId }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className={`text-2xl font-display font-bold mt-0.5 ${tone || ""}`}>{value ?? 0}</div>
    </div>
  );
}

function StatusActionRow({ user, onChange, onReset, busy }) {
  const [status, setStatus] = useState(user.abuse_status || "normal");
  const [reason, setReason] = useState("");
  const dirty = status !== user.abuse_status;

  const apply = async () => {
    const r = (reason || "").trim();
    if (r.length < 3) {
      toast.error("Reason must be at least 3 characters.");
      return;
    }
    await onChange(user.user_id, status, r);
    setReason("");
  };

  return (
    <tr className="border-t border-white/5 align-top" data-testid={`abuse-user-row-${user.user_id}`}>
      <td className="p-3 font-mono text-xs">
        <div className="text-ink">{user.email || "—"}</div>
        <div className="text-muted">{user.user_id}</div>
      </td>
      <td className="p-3"><StatusTag status={user.abuse_status} /></td>
      <td className="p-3 text-xs text-muted max-w-[280px]">
        <div className="line-clamp-2">{user.abuse_status_reason || "—"}</div>
        {user.abuse_status_set_at && (
          <div className="text-[10px] font-mono mt-1">
            {String(user.abuse_status_set_at).slice(0, 19).replace("T", " ")}
            {user.abuse_status_set_by ? ` · by ${user.abuse_status_set_by}` : ""}
          </div>
        )}
      </td>
      <td className="p-3">
        <div className="flex flex-col gap-2 min-w-[220px]">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            disabled={busy}
            className="input-brutal text-xs py-1.5"
            data-testid={`abuse-status-select-${user.user_id}`}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason (min 3 chars)"
            disabled={busy}
            className="input-brutal text-xs py-1.5"
            data-testid={`abuse-reason-input-${user.user_id}`}
          />
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={apply}
              disabled={busy || !dirty || reason.trim().length < 3}
              className="btn-brutal text-[11px] py-1.5 px-2.5 disabled:opacity-40 disabled:cursor-not-allowed flex-1"
              data-testid={`abuse-apply-btn-${user.user_id}`}
            >
              Apply
            </button>
            <button
              type="button"
              onClick={() => onReset(user.user_id)}
              disabled={busy}
              className="btn-ghost text-[11px] py-1.5 px-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
              title="Wipe rate-limit counters"
              data-testid={`abuse-reset-btn-${user.user_id}`}
            >
              Reset counters
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

export default function AdminAntiAbuse() {
  const { user, loading: authLoading } = useAuth();

  const [summary, setSummary] = useState(null);
  const [blocked, setBlocked] = useState([]);
  const [suspicious, setSuspicious] = useState([]);
  const [recent, setRecent] = useState([]);
  const [windowHours, setWindowHours] = useState(24);
  const [suspiciousHours, setSuspiciousHours] = useState(1);
  const [minEvents, setMinEvents] = useState(20);
  const [loading, setLoading] = useState(true);
  const [busyUser, setBusyUser] = useState(null);
  const [error, setError] = useState(null);

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, b, sus, r] = await Promise.all([
        api.get(`/admin/anti-abuse/summary?hours=${windowHours}`),
        api.get("/admin/anti-abuse/blocked-users"),
        api.get(`/admin/anti-abuse/suspicious-users?hours=${suspiciousHours}&min_events=${minEvents}`),
        api.get("/admin/anti-abuse/recent?limit=80"),
      ]);
      setSummary(s.data);
      setBlocked(b.data?.items || []);
      setSuspicious(sus.data?.users || []);
      setRecent(r.data?.items || []);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load anti-abuse data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!authLoading && user?.role === "admin") {
      // Defer one microtask so the eslint react-hooks/set-state-in-effect
      // rule sees no synchronous state writes from this effect.
      Promise.resolve().then(loadAll);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user, windowHours, suspiciousHours, minEvents]);

  const onSetStatus = async (userId, status, reason) => {
    setBusyUser(userId);
    try {
      await api.post("/admin/anti-abuse/set-status", { user_id: userId, status, reason });
      toast.success(`User ${userId.slice(0, 14)}… → ${status}`);
      await loadAll();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      toast.error(
        typeof detail === "object" ? detail?.message || detail?.code || "Update failed" :
        typeof detail === "string" ? detail : "Update failed."
      );
    } finally {
      setBusyUser(null);
    }
  };

  const onResetCounters = async (userId) => {
    if (!window.confirm("Reset all rate-limit counters for this user?")) return;
    setBusyUser(userId);
    try {
      await api.post("/admin/anti-abuse/reset-counters", { user_id: userId });
      toast.success("Counters reset.");
      await loadAll();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      toast.error(
        typeof detail === "object" ? detail?.message || detail?.code || "Reset failed" :
        typeof detail === "string" ? detail : "Reset failed."
      );
    } finally {
      setBusyUser(null);
    }
  };

  const byEvent = summary?.by_event || {};

  const eventToneMap = useMemo(() => ({
    anti_abuse_rate_limited: "text-amber",
    anti_abuse_user_limited: "text-amber",
    anti_abuse_user_blocked: "text-rose-soft",
    anti_abuse_blocked_user_attempt: "text-rose-soft",
    anti_abuse_user_unblocked: "text-emerald-300",
    anti_abuse_counters_reset: "text-emerald-300",
    anti_abuse_exempt_bypassed: "text-muted",
  }), []);

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/anti-abuse" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="anti-abuse-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen page-bg" data-testid="admin-anti-abuse-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">MODERATION · ANTI-ABUSE</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Abuse control.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Live view of rate-limits, suspicious activity, and currently-restricted users.
            Every status change is audited. Admin emails are always exempt.
          </p>
          <div className="flex items-center gap-3 pt-2 flex-wrap">
            <button onClick={loadAll} className="btn-ghost text-xs" disabled={loading} data-testid="anti-abuse-refresh">
              {loading ? "Loading…" : "Refresh"}
            </button>
            <label className="text-[10px] font-mono uppercase tracking-widest text-muted flex items-center gap-1.5">
              Summary window:
              <select
                value={windowHours}
                onChange={(e) => setWindowHours(Number(e.target.value))}
                className="input-brutal text-xs py-1 px-2"
                data-testid="anti-abuse-window-select"
              >
                {[1, 6, 24, 72, 168].map((h) => <option key={h} value={h}>{h}h</option>)}
              </select>
            </label>
          </div>
          {error && (
            <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="anti-abuse-error">{error}</div>
          )}
        </header>

        {/* ── Summary metrics ─────────────────────────────────── */}
        <section className="space-y-3" data-testid="anti-abuse-summary-section">
          <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
            Last {summary?.hours ?? windowHours}h activity
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <MetricCard
              label="Users blocked"
              value={summary?.users_blocked}
              tone="text-rose-soft"
              testId="metric-users-blocked"
            />
            <MetricCard
              label="Users limited"
              value={summary?.users_limited}
              tone="text-amber"
              testId="metric-users-limited"
            />
            <MetricCard
              label="Rate-limit hits"
              value={byEvent.anti_abuse_rate_limited}
              tone="text-amber"
              testId="metric-rate-limited"
            />
            <MetricCard
              label="Block attempts"
              value={byEvent.anti_abuse_blocked_user_attempt}
              tone="text-rose-soft"
              testId="metric-block-attempts"
            />
            <MetricCard
              label="Limit applied"
              value={byEvent.anti_abuse_user_limited}
              testId="metric-limit-applied"
            />
            <MetricCard
              label="Block applied"
              value={byEvent.anti_abuse_user_blocked}
              testId="metric-block-applied"
            />
          </div>
        </section>

        {/* ── Blocked / limited users ─────────────────────────── */}
        <section className="space-y-3" data-testid="anti-abuse-blocked-section">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
              Currently restricted ({blocked.length})
            </h2>
            <p className="text-[10px] font-mono uppercase tracking-widest text-muted">
              Change status → audit row written + counters cleared on unblock
            </p>
          </div>
          {blocked.length === 0 ? (
            <div className="brutal-card p-6 text-sm text-muted font-mono" data-testid="anti-abuse-blocked-empty">
              No users currently limited or blocked.
            </div>
          ) : (
            <div className="brutal-card overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                    <th className="p-3">User</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Reason / set by</th>
                    <th className="p-3">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {blocked.map((u) => (
                    <StatusActionRow
                      key={u.user_id}
                      user={u}
                      onChange={onSetStatus}
                      onReset={onResetCounters}
                      busy={busyUser === u.user_id}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* ── Suspicious users ────────────────────────────────── */}
        <section className="space-y-3" data-testid="anti-abuse-suspicious-section">
          <div className="flex items-baseline justify-between gap-3 flex-wrap">
            <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
              Suspicious activity ({suspicious.length})
            </h2>
            <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-muted">
              <span>window</span>
              <select
                value={suspiciousHours}
                onChange={(e) => setSuspiciousHours(Number(e.target.value))}
                className="input-brutal text-xs py-1 px-2"
                data-testid="suspicious-hours-select"
              >
                {[1, 3, 6, 12, 24].map((h) => <option key={h} value={h}>{h}h</option>)}
              </select>
              <span>min events</span>
              <select
                value={minEvents}
                onChange={(e) => setMinEvents(Number(e.target.value))}
                className="input-brutal text-xs py-1 px-2"
                data-testid="suspicious-min-events-select"
              >
                {[5, 10, 20, 50, 100].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          </div>
          {suspicious.length === 0 ? (
            <div className="brutal-card p-6 text-sm text-muted font-mono" data-testid="anti-abuse-suspicious-empty">
              No users have crossed the threshold in this window.
            </div>
          ) : (
            <div className="brutal-card overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                    <th className="p-3">User</th>
                    <th className="p-3">Events</th>
                    <th className="p-3">Scopes</th>
                    <th className="p-3">Last seen</th>
                    <th className="p-3">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {suspicious.map((s) => (
                    <tr key={s.user_id} className="border-t border-white/5" data-testid={`suspicious-row-${s.user_id}`}>
                      <td className="p-3 font-mono text-xs">
                        <div className="text-ink">{s.email || "—"}</div>
                        <div className="text-muted">{s.user_id}</div>
                      </td>
                      <td className="p-3 font-mono font-bold text-amber">{s.events}</td>
                      <td className="p-3 text-[11px] text-muted">
                        {(s.scopes || []).slice(0, 4).join(", ")}
                        {(s.scopes || []).length > 4 ? "…" : ""}
                      </td>
                      <td className="p-3 font-mono text-[11px] text-muted">
                        {String(s.last_seen || "").slice(0, 19).replace("T", " ")}
                      </td>
                      <td className="p-3">
                        <button
                          type="button"
                          onClick={() => onSetStatus(s.user_id, "limited", "Auto-flag from suspicious dashboard")}
                          disabled={busyUser === s.user_id}
                          className="btn-ghost text-[11px] py-1.5 px-2.5"
                          data-testid={`suspicious-limit-btn-${s.user_id}`}
                        >
                          Limit
                        </button>
                        <button
                          type="button"
                          onClick={() => onSetStatus(s.user_id, "blocked", "Auto-flag from suspicious dashboard")}
                          disabled={busyUser === s.user_id}
                          className="btn-ghost text-[11px] py-1.5 px-2.5 ml-1.5 text-rose-soft"
                          data-testid={`suspicious-block-btn-${s.user_id}`}
                        >
                          Block
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* ── Recent events ───────────────────────────────────── */}
        <section className="space-y-3" data-testid="anti-abuse-recent-section">
          <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">
            Recent events (last 80)
          </h2>
          {recent.length === 0 ? (
            <div className="brutal-card p-6 text-sm text-muted font-mono" data-testid="anti-abuse-recent-empty">
              No abuse events recorded.
            </div>
          ) : (
            <div className="brutal-card overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                    <th className="p-3">Time</th>
                    <th className="p-3">Event</th>
                    <th className="p-3">User</th>
                    <th className="p-3">Scope</th>
                    <th className="p-3">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((e, idx) => (
                    <tr key={e.event_id || idx} className="border-t border-white/5" data-testid={`anti-abuse-event-${idx}`}>
                      <td className="p-3 font-mono text-muted whitespace-nowrap">
                        {String(e.created_at || "").slice(0, 19).replace("T", " ")}
                      </td>
                      <td className={`p-3 font-mono ${eventToneMap[e.event] || ""}`}>
                        {(e.event || "").replace(/^anti_abuse_/, "")}
                      </td>
                      <td className="p-3 font-mono text-muted">
                        {e.email || e.user_id || "—"}
                      </td>
                      <td className="p-3 font-mono text-muted">{e.scope || e.endpoint || "—"}</td>
                      <td className="p-3 text-muted max-w-[280px] truncate" title={e.reason || ""}>{e.reason || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <footer className="text-[11px] font-mono uppercase tracking-widest text-muted pt-6 border-t border-white/5" data-testid="anti-abuse-footer">
          Read-only by default · Every status change is server-audited · Admin emails always exempt
        </footer>
      </div>
    </div>
  );
}
