/**
 * Debates admin — read-only-ish moderation surface.
 * - Lists all debates with status/featured controls
 * - Lists open reports with hide/restore actions on arguments
 * - Top-level metrics card
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

export default function DebatesAdmin() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState("metrics");
  const [metrics, setMetrics] = useState(null);
  const [debates, setDebates] = useState([]);
  const [reports, setReports] = useState([]);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchTab = useCallback(async () => {
    try {
      if (tab === "metrics") {
        const r = await api.get("/admin/debates/metrics?days=7");
        setMetrics(r.data);
      } else if (tab === "debates") {
        const r = await api.get("/admin/debates");
        setDebates(r.data?.debates || []);
      } else if (tab === "reports") {
        const r = await api.get("/admin/debates/reports?status=open");
        setReports(r.data?.reports || []);
      }
      setForbidden(false);
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/debates"); return; }
    if (!user) return;
    setLoading(true);
    fetchTab();
  }, [user, authLoading, navigate, fetchTab]);

  const setStatus = async (debate_id, status) => {
    try { await api.patch(`/admin/debates/${debate_id}`, { status }); toast.success(`Status: ${status}`); fetchTab(); }
    catch { toast.error("Failed"); }
  };
  const toggleFeatured = async (d) => {
    try { await api.patch(`/admin/debates/${d.debate_id || d.slug}`, { is_featured: !d.is_featured }); toast.success("Updated"); fetchTab(); }
    catch { toast.error("Failed"); }
  };
  const moderateArg = async (argument_id, moderation_status) => {
    try { await api.patch(`/admin/debates/arguments/${argument_id}`, { moderation_status, reason: "admin" }); toast.success(`Argument ${moderation_status}`); fetchTab(); }
    catch { toast.error("Failed"); }
  };

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  if (forbidden) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 py-10" data-testid="admin-debates-forbidden">
          <div className="brutal-card p-8 text-center">
            <h1 className="heading-display text-2xl mb-2">Admin only</h1>
            <p className="text-sm text-ink/70">This dashboard is for admin accounts.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="admin-debates-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Debates · admin</div>
        <h1 className="heading-display text-3xl sm:text-4xl mt-1 mb-5">Debate moderation</h1>

        <div className="flex gap-2 mb-5 flex-wrap">
          {["metrics", "debates", "reports"].map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              data-testid={`admin-debates-tab-${t}`}
              className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${tab === t ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`}
            >
              {t}
            </button>
          ))}
        </div>

        {loading && <div className="text-muted font-mono text-sm">loading…</div>}

        {tab === "metrics" && metrics && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4" data-testid="admin-debates-metrics">
            {[
              ["Debates total", metrics.debates_total],
              ["Active", metrics.debates_active],
              ["Args (7d)", metrics.arguments_total],
              ["Hidden", metrics.arguments_hidden],
              ["Votes (7d)", metrics.votes],
              ["Joined (7d)", metrics.participants_joined],
              ["Reports", metrics.reports],
              ["Hidden rate", `${metrics.hidden_rate_pct}%`],
            ].map(([label, value]) => (
              <div key={label} className="brutal-card p-4">
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
                <div className="font-display font-black text-2xl mt-1 text-ink">{value}</div>
              </div>
            ))}
          </div>
        )}

        {tab === "debates" && (
          <div className="brutal-card overflow-x-auto" data-testid="admin-debates-list">
            <table className="w-full text-sm">
              <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                <tr className="border-b border-ink/10">
                  <th className="text-left p-3">Slug · Title</th>
                  <th className="text-right p-3">Args</th>
                  <th className="text-right p-3">Votes</th>
                  <th className="text-right p-3">Status</th>
                  <th className="text-right p-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {debates.map((d) => (
                  <tr key={d.slug} className="border-b border-ink/5">
                    <td className="p-3">
                      <div className="font-mono text-xs uppercase tracking-widest text-ink/60">{d.slug}</div>
                      <div className="text-ink">{d.title}</div>
                    </td>
                    <td className="p-3 text-right tabular-nums">{d.argument_count}</td>
                    <td className="p-3 text-right tabular-nums">{d.vote_count}</td>
                    <td className="p-3 text-right">
                      <span className={`text-[10px] font-mono uppercase tracking-widest px-2 py-0.5 rounded-full ${d.status === "active" ? "bg-emerald-500/15 text-emerald-soft" : "bg-rose-500/15 text-rose-300"}`}>
                        {d.status}
                      </span>
                    </td>
                    <td className="p-3 text-right space-x-2">
                      <button onClick={() => toggleFeatured(d)} className="text-[10px] font-mono uppercase tracking-widest text-amber hover:underline">
                        {d.is_featured ? "Unfeature" : "Feature"}
                      </button>
                      {d.status === "active" ? (
                        <button onClick={() => setStatus(d.slug, "ended")} className="text-[10px] font-mono uppercase tracking-widest text-rose-300 hover:underline">End</button>
                      ) : (
                        <button onClick={() => setStatus(d.slug, "active")} className="text-[10px] font-mono uppercase tracking-widest text-emerald-soft hover:underline">Reopen</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "reports" && (
          <div className="space-y-3" data-testid="admin-debates-reports">
            {reports.length === 0 && <div className="brutal-card p-6 text-center text-xs font-mono text-muted">No open reports.</div>}
            {reports.map((r) => (
              <div key={r.report_id} className="brutal-card p-4">
                <div className="flex items-center justify-between gap-2 mb-2 text-[11px] font-mono uppercase tracking-widest text-muted">
                  <span>{new Date(r.created_at).toLocaleString()}</span>
                  <span>{r.reason}</span>
                </div>
                {r.argument && (
                  <div className="text-sm text-ink/85 italic border-l-2 border-rose-300 pl-3 mb-2 line-clamp-3">"{r.argument.content}"</div>
                )}
                <div className="flex items-center gap-2">
                  {r.argument && r.argument.moderation_status !== "hidden" && (
                    <button onClick={() => moderateArg(r.argument.argument_id, "hidden")} className="btn-ghost text-xs">Hide argument</button>
                  )}
                  {r.argument && r.argument.moderation_status === "hidden" && (
                    <button onClick={() => moderateArg(r.argument.argument_id, "visible")} className="btn-ghost text-xs">Restore</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="mt-8 text-[10px] font-mono text-muted">
          <Link to="/admin/anonymous-metrics" className="hover:text-ink underline">Anonymous metrics</Link>
          {" · "}
          <Link to="/admin/voice-metrics" className="hover:text-ink underline">Voice metrics</Link>
        </div>
      </div>
    </div>
  );
}
