/**
 * Admin · Avatar Chat metrics + job queue.
 * Read-only on health metrics. Retry / cancel actions for individual jobs.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function num(n) { return n === null || n === undefined ? "—" : (typeof n === "number" && n >= 1000 ? n.toLocaleString() : String(n)); }

function StatCard({ label, value, sub, testid }) {
  return (
    <div className="brutal-card p-4 sm:p-5" data-testid={testid}>
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display font-black text-2xl sm:text-3xl mt-1 text-ink break-words">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

export default function AdminAvatarChat() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [metrics, setMetrics] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [filter, setFilter] = useState("");
  const [days, setDays] = useState(7);
  const [forbidden, setForbidden] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [m, j] = await Promise.all([
        api.get(`/admin/avatar-chat/metrics?days=${days}`),
        api.get(`/admin/avatar-chat/jobs${filter ? `?status=${filter}&limit=200` : "?limit=200"}`),
      ]);
      setMetrics(m.data);
      setJobs(j.data?.jobs || []);
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
    }
  }, [days, filter]);

  useEffect(() => {
    if (!loading && !user) { navigate("/login?redirect=/admin/avatar-chat"); return; }
    if (user) refresh();
  }, [user, loading, navigate, refresh]);

  const retry = async (id) => { try { await api.post(`/admin/avatar-chat/jobs/${id}/retry`); toast.success("Retrying"); await refresh(); } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); } };
  const cancel = async (id) => { if (!window.confirm("Cancel job?")) return; try { await api.post(`/admin/avatar-chat/jobs/${id}/cancel`); toast.success("Cancelled"); await refresh(); } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); } };

  if (loading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;
  if (forbidden) return <div className="page-bg min-h-screen min-h-[100dvh]"><Navbar /><div className="max-w-3xl mx-auto px-4 py-10"><div className="brutal-card p-8 text-center" data-testid="admin-avatar-forbidden"><h1 className="heading-display text-2xl">Admin only</h1></div></div></div>;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="admin-avatar-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-3">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Admin · Avatar chat</div>
            <h1 className="heading-display text-2xl sm:text-3xl mt-1">Avatar pipeline</h1>
            <p className="text-xs text-muted mt-1 max-w-xl">Render queue, failure codes, retry/cancel.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 7, 14, 30].map((d) => (
              <button key={d} onClick={() => setDays(d)} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`admin-avatar-window-${d}d`}>{d === 1 ? "24h" : `${d}d`}</button>
            ))}
          </div>
        </div>

        {metrics && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-5">
              <StatCard testid="avatar-stat-total" label="Total" value={num(metrics.total)} />
              <StatCard testid="avatar-stat-completed" label="Completed" value={num(metrics.completed)} />
              <StatCard testid="avatar-stat-failed" label="Failed" value={num(metrics.failed)} />
              <StatCard testid="avatar-stat-queue" label="Queue size" value={num(metrics.queue_size)} sub="across all stages" />
              <StatCard testid="avatar-stat-render" label="Avg render time" value={`${metrics.avg_render_sec || 0}s`} />
              <StatCard testid="avatar-stat-tts" label="TTS" value={metrics.tts_configured ? "on" : "off"} sub="EMERGENT_LLM_KEY" />
              <StatCard testid="avatar-stat-lipsync" label="Lip-sync" value={metrics.lipsync_configured ? "on" : "off"} sub="FAL_KEY" />
              <StatCard testid="avatar-stat-public" label="Public flag" value={metrics.feature_enabled_public ? "on" : "off"} sub="AVATAR_CHAT_ENABLED" />
            </div>

            {metrics.errors_by_code?.length > 0 && (
              <div className="brutal-card p-4 mb-5" data-testid="avatar-errors-by-code">
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Errors by code</div>
                <div className="flex flex-wrap gap-2">
                  {metrics.errors_by_code.map((e) => (
                    <span key={e.code} className="text-xs font-mono px-2 py-1 rounded-full border border-red-400/40 text-red-300 bg-red-500/10">{e.code} · {e.count}</span>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center gap-2 mb-3">
              <span className="text-[11px] font-mono uppercase tracking-widest text-muted">Filter:</span>
              {["", "queued", "generating_audio", "rendering_video", "completed", "failed"].map((s) => (
                <button key={s || "all"} onClick={() => setFilter(s)} className={`px-2 py-1 rounded-full text-[10px] font-mono uppercase tracking-widest border ${filter === s ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`avatar-filter-${s || "all"}`}>{s || "all"}</button>
              ))}
            </div>

            <div className="brutal-card overflow-x-auto" data-testid="avatar-jobs-table">
              <table className="w-full text-sm">
                <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                  <tr className="border-b border-ink/10">
                    <th className="text-left p-3">Message</th>
                    <th className="text-left p-3">Status</th>
                    <th className="text-left p-3">Created</th>
                    <th className="text-right p-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.length === 0 && <tr><td colSpan={4} className="p-4 text-center text-muted text-xs">No jobs.</td></tr>}
                  {jobs.map((j) => (
                    <tr key={j.message_id} className="border-b border-ink/5" data-testid={`avatar-job-row-${j.message_id}`}>
                      <td className="p-3 max-w-xs">
                        <div className="text-xs text-ink/80 line-clamp-2">{j.input_text}</div>
                        <div className="text-[10px] font-mono text-muted mt-1">{j.message_id}</div>
                      </td>
                      <td className="p-3 text-xs font-mono uppercase tracking-widest">{j.video_status}{j.error_code ? ` · ${j.error_code}` : ""}</td>
                      <td className="p-3 text-[10px] font-mono text-muted">{new Date(j.created_at).toLocaleString()}</td>
                      <td className="p-3 text-right">
                        {j.video_status === "failed" && <button onClick={() => retry(j.message_id)} className="btn-ghost text-xs" data-testid={`avatar-job-retry-${j.message_id}`}>Retry</button>}
                        {(j.video_status === "queued" || j.video_status === "rendering_video" || j.video_status === "generating_audio") && <button onClick={() => cancel(j.message_id)} className="btn-ghost text-xs text-red-300" data-testid={`avatar-job-cancel-${j.message_id}`}>Cancel</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-5 text-[10px] font-mono text-muted">
              <Link to="/admin/delayed-messages" className="hover:text-ink underline">Delayed messages</Link>
              {" · "}<Link to="/admin/safety" className="hover:text-ink underline">Safety</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
