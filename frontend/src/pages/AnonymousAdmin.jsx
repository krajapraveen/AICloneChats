import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

export default function AnonymousAdmin() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState("metrics"); // metrics | reports | flagged | transcript
  const [metrics, setMetrics] = useState(null);
  const [reports, setReports] = useState([]);
  const [flagged, setFlagged] = useState([]);
  const [transcriptSlug, setTranscriptSlug] = useState("loneliness");
  const [transcript, setTranscript] = useState([]);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/anonymous-reality"); return; }
    if (!user) return;
    setLoading(true);
    api.get("/admin/anonymous/metrics?days=7").then((r) => { setMetrics(r.data); setForbidden(false); })
      .catch((e) => { if (e?.response?.status === 403) setForbidden(true); })
      .finally(() => setLoading(false));
  }, [user, authLoading, navigate]);

  useEffect(() => {
    if (!user || forbidden) return;
    if (tab === "reports") api.get("/admin/anonymous/reports?status=open").then((r) => setReports(r.data?.reports || [])).catch(() => {});
    if (tab === "flagged") api.get("/admin/anonymous/messages/flagged").then((r) => setFlagged(r.data?.messages || [])).catch(() => {});
    if (tab === "transcript") api.get(`/admin/anonymous/rooms/${transcriptSlug}/transcript`).then((r) => setTranscript(r.data?.messages || [])).catch(() => {});
  }, [tab, transcriptSlug, user, forbidden]);

  const removeMsg = async (mid) => {
    if (!window.confirm("Remove this message?")) return;
    try { await api.post(`/admin/anonymous/messages/${mid}/remove`, { reason: "admin" }); toast.success("Removed"); setFlagged((p) => p.filter((m) => m.message_id !== mid)); setTranscript((p) => p.map((m) => m.message_id === mid ? { ...m, moderation_status: "admin_removed" } : m)); }
    catch { toast.error("Could not remove"); }
  };

  const banSession = async (sid) => {
    if (!sid) return;
    if (!window.confirm("Ban this anonymous session? They cannot post anymore.")) return;
    try { await api.post(`/admin/anonymous/sessions/${sid}/ban`, { reason: "admin" }); toast.success("Banned"); }
    catch { toast.error("Could not ban"); }
  };

  const freezeRoom = async (slug, freeze) => {
    if (!window.confirm(`${freeze ? "Freeze" : "Unfreeze"} ${slug}?`)) return;
    try {
      if (freeze) await api.post(`/admin/anonymous/rooms/${slug}/freeze`, { reason: "admin" });
      else await api.post(`/admin/anonymous/rooms/${slug}/unfreeze`);
      toast.success(`${freeze ? "Frozen" : "Unfrozen"}`);
      const r = await api.get("/admin/anonymous/metrics?days=7"); setMetrics(r.data);
    } catch { toast.error("Failed"); }
  };

  const resolveReport = async (rid) => {
    try { await api.post(`/admin/anonymous/reports/${rid}/resolve`, { reason: "reviewed" }); setReports((p) => p.filter((r) => r.report_id !== rid)); toast.success("Resolved"); } catch { toast.error("Failed"); }
  };

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  if (forbidden) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 py-10" data-testid="anon-admin-forbidden">
          <div className="brutal-card p-8 text-center">
            <h1 className="heading-display text-2xl mb-2">Admin only</h1>
            <p className="text-sm text-ink/70">This dashboard is for admin accounts.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6 sm:py-8" data-testid="anon-admin-page">
        <div className="flex items-end justify-between mb-5 flex-wrap gap-3">
          <div>
            <span className="tag tag-rose mb-2 inline-block">ANONYMOUS · ADMIN</span>
            <h1 className="heading-display text-2xl sm:text-3xl">Moderation desk</h1>
            <p className="text-xs text-muted font-mono mt-1">Read transcripts. Remove first, ban if needed. Overprotective is fine in week 1.</p>
          </div>
          <Link to="/anonymous-reality" className="btn-ghost text-sm" data-testid="anon-admin-view-rooms">View rooms →</Link>
        </div>

        <div className="flex gap-1 p-1 rounded-2xl bg-black/30 border border-white/5 mb-5 overflow-x-auto" data-testid="anon-admin-tabs">
          {[{ id: "metrics", label: "Metrics" }, { id: "reports", label: `Reports${reports.length ? ` · ${reports.length}` : ""}` }, { id: "flagged", label: `Flagged${flagged.length ? ` · ${flagged.length}` : ""}` }, { id: "transcript", label: "Read the room" }].map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className={`text-xs font-display font-bold py-2 px-4 rounded-xl whitespace-nowrap transition ${tab === t.id ? "bg-ink text-bg" : "text-ink/70 hover:text-ink"}`} data-testid={`anon-admin-tab-${t.id}`}>
              {t.label}
            </button>
          ))}
        </div>

        {loading && tab === "metrics" ? (
          <div className="text-muted font-mono text-sm">loading…</div>
        ) : tab === "metrics" && metrics ? (
          <div className="space-y-5" data-testid="anon-admin-metrics">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="brutal-card p-4"><div className="text-[10px] font-mono uppercase tracking-widest text-muted">USER MESSAGES · 7d</div><div className="font-display font-black text-2xl mt-1">{metrics.total_user_messages}</div></div>
              <div className="brutal-card p-4 border-rose"><div className="text-[10px] font-mono uppercase tracking-widest text-muted">BLOCK RATE</div><div className="font-display font-black text-2xl mt-1">{metrics.block_rate_pct}%</div><div className="text-xs text-muted">{metrics.blocked_messages} blocked</div></div>
              <div className="brutal-card p-4"><div className="text-[10px] font-mono uppercase tracking-widest text-muted">REPORTS</div><div className="font-display font-black text-2xl mt-1">{metrics.reports}</div></div>
              <div className="brutal-card p-4"><div className="text-[10px] font-mono uppercase tracking-widest text-muted">SESSIONS · 7d</div><div className="font-display font-black text-2xl mt-1">{metrics.sessions_created}</div></div>
            </div>
            <div className="glass-card p-5">
              <h2 className="heading-display text-lg mb-3">Rooms</h2>
              <div className="space-y-1.5">
                {metrics.rooms.map((r) => (
                  <div key={r.slug} className="flex items-center justify-between gap-2 text-sm py-2 border-b border-white/5" data-testid={`anon-admin-room-row-${r.slug}`}>
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <span className={`inline-block w-2 h-2 rounded-full ${r.status === "frozen" ? "bg-amber" : "bg-emerald"}`} />
                      <span className="font-display font-bold truncate">{r.title}</span>
                      <span className="text-[10px] font-mono text-muted shrink-0">{r.active_count} live · {r.messages} msgs · {fmtTime(r.last_message_at)}</span>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      <button onClick={() => { setTranscriptSlug(r.slug); setTab("transcript"); }} className="btn-ghost text-xs" data-testid={`anon-admin-read-${r.slug}`}>Read</button>
                      <button onClick={() => freezeRoom(r.slug, r.status !== "frozen")} className="btn-ghost text-xs" data-testid={`anon-admin-freeze-${r.slug}`}>{r.status === "frozen" ? "Unfreeze" : "Freeze"}</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : tab === "reports" ? (
          <div className="space-y-3" data-testid="anon-admin-reports">
            {reports.length === 0 && <div className="text-muted font-mono text-sm">No open reports.</div>}
            {reports.map((r) => (
              <div key={r.report_id} className="brutal-card p-4" data-testid={`anon-admin-report-${r.report_id}`}>
                <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                  <span className="tag tag-rose text-[10px]">REPORT · {r.room_slug}</span>
                  <span className="text-[10px] font-mono text-muted">{fmtTime(r.created_at)}</span>
                </div>
                <p className="text-xs text-muted mb-2">Reason: {r.reason}</p>
                {r.message ? (
                  <div className="rounded-lg bg-black/30 border border-white/5 p-3 mb-3">
                    <div className="text-[10px] font-mono text-muted mb-1">{r.message.anonymous_handle} · {fmtTime(r.message.created_at)}</div>
                    <p className="text-sm text-ink/90 whitespace-pre-wrap">{r.message.content}</p>
                  </div>
                ) : <div className="text-xs text-muted">Message not found.</div>}
                <div className="flex flex-wrap gap-2">
                  {r.message && r.message.moderation_status !== "admin_removed" && (
                    <button onClick={() => removeMsg(r.message_id)} className="btn-brutal text-xs" data-testid={`anon-admin-report-remove-${r.report_id}`}>Remove message</button>
                  )}
                  {r.reported_session_id && <button onClick={() => banSession(r.reported_session_id)} className="btn-ghost text-xs" data-testid={`anon-admin-report-ban-${r.report_id}`}>Ban session</button>}
                  <button onClick={() => resolveReport(r.report_id)} className="btn-ghost text-xs" data-testid={`anon-admin-report-resolve-${r.report_id}`}>Resolve</button>
                </div>
              </div>
            ))}
          </div>
        ) : tab === "flagged" ? (
          <div className="space-y-3" data-testid="anon-admin-flagged">
            {flagged.length === 0 && <div className="text-muted font-mono text-sm">No auto-blocked messages yet.</div>}
            {flagged.map((m) => (
              <div key={m.message_id} className="brutal-card p-4">
                <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                  <span className="tag tag-rose text-[10px]">{(m.moderation_category || "blocked").toUpperCase()} · {m.room_slug}</span>
                  <span className="text-[10px] font-mono text-muted">{fmtTime(m.created_at)} · {m.anonymous_handle}</span>
                </div>
                <p className="text-sm text-ink/85 whitespace-pre-wrap mb-3">{m.content}</p>
                <div className="flex gap-2">
                  {m.session_id && <button onClick={() => banSession(m.session_id)} className="btn-ghost text-xs">Ban session</button>}
                </div>
              </div>
            ))}
          </div>
        ) : tab === "transcript" ? (
          <div data-testid="anon-admin-transcript">
            <div className="flex flex-wrap gap-2 mb-3 items-center">
              <span className="text-xs font-mono text-muted">Reading:</span>
              <select value={transcriptSlug} onChange={(e) => setTranscriptSlug(e.target.value)} className="input-brutal text-sm py-1.5 px-2" data-testid="anon-admin-transcript-room-select">
                {(metrics?.rooms || []).map((r) => <option key={r.slug} value={r.slug}>{r.title}</option>)}
              </select>
              <span className="text-xs font-mono text-muted">{transcript.length} messages (chronological)</span>
            </div>
            <div className="brutal-card p-4 max-h-[70vh] overflow-y-auto space-y-2">
              {transcript.map((m) => (
                <div key={m.message_id} className={`p-2 rounded-lg ${m.moderation_status === "blocked" ? "bg-rose-500/10 border border-rose-400/20" : m.moderation_status === "admin_removed" ? "bg-black/40 opacity-50" : m.message_type === "system" ? "bg-violet-500/10 border border-violet-400/20" : m.message_type === "seed" ? "bg-white/5" : ""}`} data-testid={`anon-admin-tx-msg-${m.message_id}`}>
                  <div className="text-[10px] font-mono text-muted mb-0.5 flex items-center gap-2 flex-wrap">
                    <span>{m.anonymous_handle}</span>
                    <span>·</span>
                    <span>{fmtTime(m.created_at)}</span>
                    {m.message_type !== "user" && <span className="text-amber-soft">[{m.message_type}]</span>}
                    {m.moderation_status === "blocked" && <span className="text-rose-300">[BLOCKED · {m.moderation_category}]</span>}
                    {m.moderation_status === "admin_removed" && <span className="text-rose-300">[REMOVED]</span>}
                  </div>
                  <p className="text-sm text-ink/90 whitespace-pre-wrap">{m.content}</p>
                  {m.message_type === "user" && m.moderation_status !== "admin_removed" && (
                    <button onClick={() => removeMsg(m.message_id)} className="text-[10px] font-mono uppercase tracking-widest text-rose-300/70 hover:text-rose-300 mt-1">Remove</button>
                  )}
                </div>
              ))}
              {transcript.length === 0 && <div className="text-muted font-mono text-sm py-6 text-center">No messages yet.</div>}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
