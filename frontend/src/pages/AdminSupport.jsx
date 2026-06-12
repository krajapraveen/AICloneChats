import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import { useNavigate } from "react-router-dom";

function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

const STATUS_OPTIONS = ["", "open", "awaiting_user", "resolved", "closed"];

function StatusPill({ status }) {
  const map = {
    open: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10",
    awaiting_user: "border-amber/40 text-amber bg-amber/10",
    resolved: "border-violet/40 text-violet-soft bg-violet/10",
    closed: "border-white/15 text-muted bg-white/[0.03]",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${map[status] || ""}`}>
      {status?.replace("_", " ")}
    </span>
  );
}

function ThreadDetail({ threadId, onBack, onChanged }) {
  const [thread, setThread] = useState(null);
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);

  const load = () => {
    setLoading(true);
    api.get(`/admin/support/threads/${threadId}`)
      .then((r) => setThread(r.data))
      .catch(() => setThread(null))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [threadId]);

  const send = async (e) => {
    e.preventDefault();
    if (reply.trim().length < 2) return;
    setSending(true);
    try {
      await api.post(`/admin/support/threads/${threadId}/reply`, { body: reply.trim() });
      setReply("");
      load();
      onChanged?.();
      toast.success("Reply sent.");
    } catch (err) {
      toast.error(err?.response?.data?.detail?.message || "Could not send.");
    } finally {
      setSending(false);
    }
  };

  const setStatus = async (status) => {
    try {
      await api.post(`/admin/support/threads/${threadId}/status`, { status });
      load();
      onChanged?.();
      toast.success(`Marked ${status}.`);
    } catch {
      toast.error("Could not update status.");
    }
  };

  if (loading) return <p className="text-sm text-muted">Loading…</p>;
  if (!thread) return <p className="text-sm text-rose-soft">Thread not found.</p>;

  return (
    <div data-testid="admin-thread-detail">
      <button type="button" onClick={onBack} className="text-[11px] font-mono uppercase tracking-widest text-amber mb-3" data-testid="admin-thread-back">← Back to threads</button>
      <div className="brutal-card p-5">
        <div className="flex items-start justify-between gap-3 flex-wrap mb-2">
          <div>
            <h3 className="font-display text-xl">{thread.subject}</h3>
            <p className="text-[11px] font-mono uppercase tracking-widest text-muted mt-0.5">
              {thread.kind} · from {thread.user_email}
            </p>
          </div>
          <StatusPill status={thread.status} />
        </div>
        <div className="flex gap-2 mb-5 flex-wrap">
          {["open", "awaiting_user", "resolved", "closed"].map((s) => (
            <button key={s} type="button" onClick={() => setStatus(s)}
                    className={`px-2.5 py-1 rounded-lg text-[10px] font-mono uppercase tracking-widest border transition ${
                      thread.status === s ? "border-amber/60 bg-amber/15 text-amber" : "border-white/10 bg-white/[0.03] text-ink/70 hover:bg-white/[0.07]"
                    }`}
                    data-testid={`admin-set-status-${s}`}>
              {s.replace("_", " ")}
            </button>
          ))}
        </div>

        <div className="space-y-3 mb-5 max-h-[480px] overflow-y-auto pr-1">
          {(thread.messages || []).map((m) => (
            <div key={m.message_id}
                 className={`p-3 rounded-lg border ${m.sender === "admin" ? "bg-amber/5 border-amber/30" : "bg-white/[0.03] border-white/10"}`}
                 data-testid={`admin-msg-${m.message_id}`}>
              <div className="flex items-center justify-between gap-2 text-[10px] font-mono uppercase tracking-widest mb-1">
                <span className={m.sender === "admin" ? "text-amber" : "text-ink/70"}>
                  {m.sender.toUpperCase()} · {m.sender_email}
                </span>
                <span className="text-muted">{formatDate(m.created_at)}</span>
              </div>
              <p className="text-sm whitespace-pre-wrap text-ink/90">{m.body}</p>
            </div>
          ))}
        </div>

        <form onSubmit={send} className="space-y-2" data-testid="admin-reply-form">
          <textarea
            className="input-brutal min-h-[110px]"
            placeholder="Reply to user…"
            value={reply}
            onChange={(e) => setReply(e.target.value.slice(0, 4000))}
            data-testid="admin-reply-input"
            maxLength={4000}
          />
          <div className="flex justify-end">
            <button type="submit" disabled={sending || reply.trim().length < 2} className="btn-brutal text-xs disabled:opacity-50" data-testid="admin-reply-send-btn">
              {sending ? "Sending…" : "Send reply"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function AdminSupport() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [threads, setThreads] = useState([]);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [tloading, setTloading] = useState(true);

  useEffect(() => {
    if (!loading && (!user || user.role !== "admin")) navigate("/", { replace: true });
  }, [user, loading, navigate]);

  const load = () => {
    setTloading(true);
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (unreadOnly) params.set("unread_only", "true");
    api.get(`/admin/support/threads?${params.toString()}`)
      .then((r) => {
        setThreads(r.data?.items || []);
        setUnreadTotal(r.data?.unread_total || 0);
      })
      .finally(() => setTloading(false));
  };

  useEffect(() => { if (user?.role === "admin") load(); /* eslint-disable-next-line */ }, [user, statusFilter, unreadOnly]);

  if (loading || !user) {
    return <div className="page-bg min-h-screen flex items-center justify-center">Loading…</div>;
  }

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-12">
        <p className="font-mono text-[11px] uppercase tracking-widest text-violet mb-2">aiclonechats.com · admin</p>
        <h1 className="heading-display text-3xl sm:text-4xl mb-1" data-testid="admin-support-title">Concerns & Recommendations</h1>
        <p className="text-sm text-muted mb-6">User messages. Reply, mark resolved, or close threads.</p>

        {selectedId ? (
          <ThreadDetail threadId={selectedId} onBack={() => { setSelectedId(null); load(); }} onChanged={load} />
        ) : (
          <>
            <div className="flex items-center gap-3 mb-5 flex-wrap" data-testid="admin-filters">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="input-brutal w-auto text-sm py-1.5"
                data-testid="admin-status-filter"
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s || "all"} value={s}>{s ? s.replace("_", " ") : "All statuses"}</option>
                ))}
              </select>
              <label className="text-xs text-muted flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} data-testid="admin-unread-only" />
                Unread only
              </label>
              <div className="ml-auto text-[11px] font-mono uppercase tracking-widest text-amber">
                {unreadTotal} unread total
              </div>
            </div>

            {tloading && <p className="text-sm text-muted">Loading…</p>}
            {!tloading && threads.length === 0 && (
              <p className="text-sm text-muted py-6" data-testid="admin-threads-empty">No threads match.</p>
            )}
            {!tloading && threads.length > 0 && (
              <div className="space-y-2" data-testid="admin-threads-list">
                {threads.map((t) => (
                  <button
                    key={t.thread_id}
                    type="button"
                    onClick={() => setSelectedId(t.thread_id)}
                    className="w-full text-left brutal-card p-4 hover:bg-white/[0.06] transition flex items-center justify-between gap-4"
                    data-testid={`admin-thread-${t.thread_id}`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className="font-display text-base truncate">{t.subject}</span>
                        {t.unread_for_admins && (
                          <span className="px-1.5 py-0.5 rounded-full bg-amber text-black text-[9px] font-bold uppercase tracking-widest">NEW</span>
                        )}
                      </div>
                      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">
                        {t.kind} · from {t.user_email} · {formatDate(t.last_message_at)}
                      </div>
                    </div>
                    <StatusPill status={t.status} />
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
