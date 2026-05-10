/**
 * Admin · Delayed Messages metrics + queue.
 * Force-deliver / cancel actions.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function num(n) { return n === null || n === undefined ? "—" : (typeof n === "number" && n >= 1000 ? n.toLocaleString() : String(n)); }
function fmt(iso) { try { return new Date(iso).toLocaleString(); } catch { return iso; } }

function StatCard({ label, value, sub, testid }) {
  return (
    <div className="brutal-card p-4 sm:p-5" data-testid={testid}>
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display font-black text-2xl sm:text-3xl mt-1 text-ink break-words">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

export default function AdminDelayedMessages() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [metrics, setMetrics] = useState(null);
  const [queue, setQueue] = useState([]);
  const [filter, setFilter] = useState("");
  const [days, setDays] = useState(7);
  const [forbidden, setForbidden] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [m, q] = await Promise.all([
        api.get(`/admin/delayed-messages/metrics?days=${days}`),
        api.get(`/admin/delayed-messages/queue${filter ? `?status=${filter}&limit=200` : "?limit=200"}`),
      ]);
      setMetrics(m.data);
      setQueue(q.data?.queue || []);
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
    }
  }, [days, filter]);

  useEffect(() => {
    if (!loading && !user) { navigate("/login?redirect=/admin/delayed-messages"); return; }
    if (user) refresh();
  }, [user, loading, navigate, refresh]);

  const force = async (id) => { try { await api.post(`/admin/delayed-messages/${id}/force-deliver`); toast.success("Delivering"); await refresh(); } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); } };
  const cancel = async (id) => { if (!window.confirm("Cancel?")) return; try { await api.post(`/admin/delayed-messages/${id}/cancel`); toast.success("Cancelled"); await refresh(); } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); } };

  if (loading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;
  if (forbidden) return <div className="page-bg min-h-screen min-h-[100dvh]"><Navbar /><div className="max-w-3xl mx-auto px-4 py-10"><div className="brutal-card p-8 text-center" data-testid="admin-delayed-forbidden"><h1 className="heading-display text-2xl">Admin only</h1></div></div></div>;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="admin-delayed-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-3">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Admin · Delayed messages</div>
            <h1 className="heading-display text-2xl sm:text-3xl mt-1">Delivery queue</h1>
            <p className="text-xs text-muted mt-1 max-w-xl">Scheduled, queued, delivered, failed, cancelled. Force-deliver to bypass timing.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 7, 14, 30].map((d) => (
              <button key={d} onClick={() => setDays(d)} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`admin-delayed-window-${d}d`}>{d === 1 ? "24h" : `${d}d`}</button>
            ))}
          </div>
        </div>

        {metrics && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-5">
              <StatCard testid="delayed-stat-scheduled" label="Scheduled" value={num(metrics.scheduled)} sub="active" />
              <StatCard testid="delayed-stat-queued" label="Queued" value={num(metrics.queued)} />
              <StatCard testid="delayed-stat-delivered" label="Delivered" value={num(metrics.delivered_in_window)} sub={`${days}d`} />
              <StatCard testid="delayed-stat-failed" label="Failed" value={num(metrics.failed_in_window)} sub={`${days}d`} />
              <StatCard testid="delayed-stat-cancelled" label="Cancelled" value={num(metrics.cancelled_in_window)} sub={`${days}d`} />
              <StatCard testid="delayed-stat-due" label="Due now" value={num(metrics.due_now)} sub="awaiting next tick" />
              <StatCard testid="delayed-stat-latency" label="Avg latency" value={`${metrics.avg_delivery_latency_sec || 0}s`} sub="actual − scheduled" />
              <StatCard testid="delayed-stat-cron" label="Scheduler" value={metrics.scheduler_enabled ? "on" : "off"} sub={metrics.email_configured ? "email: on" : "email: off"} />
            </div>

            {/* Persistence signals — the gravity layer. NOT engagement. */}
            <div className="mb-3">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Persistence signals · the only thing that matters</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4" data-testid="delayed-persistence-signals">
                <StatCard testid="delayed-stat-d7-open" label="D7 voluntary opens" value={metrics.d7_open_rate?.pct !== null && metrics.d7_open_rate?.pct !== undefined ? `${metrics.d7_open_rate.pct}%` : "—"} sub={`${num(metrics.d7_open_rate?.opened)} of ${num(metrics.d7_open_rate?.eligible)} delivered ≥7d ago`} />
                <StatCard testid="delayed-stat-d30-open" label="D30 voluntary opens" value={metrics.d30_open_rate?.pct !== null && metrics.d30_open_rate?.pct !== undefined ? `${metrics.d30_open_rate.pct}%` : "—"} sub={`${num(metrics.d30_open_rate?.opened)} of ${num(metrics.d30_open_rate?.eligible)} delivered ≥30d ago`} />
                <StatCard testid="delayed-stat-voluntary-opens" label="Voluntary opens" value={num(metrics.voluntary_opens_in_window)} sub={`${days}d window`} />
                <StatCard testid="delayed-stat-repeat-composers" label="Repeat composers" value={num(metrics.repeat_composers_in_window)} sub="users who scheduled ≥2" />
                <StatCard testid="delayed-stat-future-self" label="Future-self" value={num(metrics.future_self_count)} sub="messages to self" />
                <StatCard testid="delayed-stat-other-user" label="To another user" value={num(metrics.other_user_count)} sub="in-app delivery" />
                <StatCard testid="delayed-stat-email-recipient" label="To email" value={num(metrics.email_recipient_count)} sub="external delivery" />
                <StatCard testid="delayed-stat-thesis-flag" label="Thesis" value="memory" sub="not engagement" />
              </div>
              <div className="text-[10px] font-mono text-muted/80 mt-3" data-testid="delayed-thesis-note">
                {metrics.operator_note || "The system delivers; it does not chase."}
              </div>
            </div>

            {metrics.by_emotional_category?.length > 0 && (
              <div className="brutal-card p-4 mb-5" data-testid="delayed-by-cat">
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">By emotional category</div>
                <div className="flex flex-wrap gap-2">
                  {metrics.by_emotional_category.map((c) => (
                    <span key={c.category} className="text-xs font-mono px-2 py-1 rounded-full border border-ink/20">{c.category} · {c.count}</span>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center gap-2 mb-3">
              <span className="text-[11px] font-mono uppercase tracking-widest text-muted">Filter:</span>
              {["", "scheduled", "queued", "delivered", "failed", "cancelled"].map((s) => (
                <button key={s || "all"} onClick={() => setFilter(s)} className={`px-2 py-1 rounded-full text-[10px] font-mono uppercase tracking-widest border ${filter === s ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`delayed-filter-${s || "all"}`}>{s || "all"}</button>
              ))}
            </div>

            <div className="brutal-card overflow-x-auto" data-testid="delayed-queue-table">
              <table className="w-full text-sm">
                <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                  <tr className="border-b border-ink/10">
                    <th className="text-left p-3">Message</th>
                    <th className="text-left p-3">Status</th>
                    <th className="text-left p-3">Deliver at</th>
                    <th className="text-left p-3">Channel</th>
                    <th className="text-right p-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.length === 0 && <tr><td colSpan={5} className="p-4 text-center text-muted text-xs">No messages.</td></tr>}
                  {queue.map((m) => (
                    <tr key={m.delayed_message_id} className="border-b border-ink/5" data-testid={`delayed-row-${m.delayed_message_id}`}>
                      <td className="p-3 max-w-xs">
                        <div className="text-sm text-ink/85 truncate">{m.title}</div>
                        <div className="text-[10px] font-mono text-muted mt-1">{m.delayed_message_id} · {m.emotional_category}</div>
                      </td>
                      <td className="p-3 text-xs font-mono uppercase tracking-widest">{m.status}</td>
                      <td className="p-3 text-[10px] font-mono text-muted">{fmt(m.delivery_time)}</td>
                      <td className="p-3 text-[10px] font-mono">{m.delivery_channel} · {m.recipient_type}</td>
                      <td className="p-3 text-right">
                        {(m.status === "scheduled" || m.status === "queued" || m.status === "failed") && <button onClick={() => force(m.delayed_message_id)} className="btn-ghost text-xs" data-testid={`delayed-force-${m.delayed_message_id}`}>Force</button>}
                        {(m.status === "scheduled" || m.status === "queued") && <button onClick={() => cancel(m.delayed_message_id)} className="btn-ghost text-xs text-red-300" data-testid={`delayed-row-cancel-${m.delayed_message_id}`}>Cancel</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-5 text-[10px] font-mono text-muted">
              <Link to="/admin/avatar-chat" className="hover:text-ink underline">Avatar chat</Link>
              {" · "}<Link to="/admin/safety" className="hover:text-ink underline">Safety</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
