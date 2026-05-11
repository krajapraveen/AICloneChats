/**
 * Admin Chats — unified read-only chat monitoring across all surfaces.
 *
 * Privacy: chats are reviewed for safety, abuse prevention, and service
 * improvement. Sensitive values (emails, phones, API keys, passwords,
 * addresses) are auto-redacted server-side before being shown here.
 *
 * Capabilities:
 *  - Filter by chat type (clone / anonymous / debate / smart_reply)
 *  - Search text + filter by user_id + safety filter
 *  - Open full thread in side drawer
 *  - Flag / hide a conversation (logs to chat_audit_logs)
 *  - JSON export
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const CHAT_TYPES = [
  { value: "all", label: "All" },
  { value: "clone", label: "Clone Chat" },
  { value: "anonymous", label: "Anonymous" },
  { value: "debate", label: "Debate" },
  { value: "smart_reply", label: "Smart Reply" },
];

const SAFETY_FILTERS = [
  { value: "all", label: "Any safety" },
  { value: "flagged", label: "Flagged" },
  { value: "hidden", label: "Hidden" },
  { value: "blocked", label: "Blocked" },
];

function fmt(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function RedactionTags({ tags }) {
  if (!tags || !tags.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1" aria-label="redactions applied">
      {tags.map((t) => (
        <span key={t} className="text-[9px] font-mono uppercase tracking-widest px-1.5 py-0.5 rounded-sm bg-amber-500/10 text-amber-soft border border-amber-500/30">
          redacted:{t}
        </span>
      ))}
    </div>
  );
}

function ChatRow({ row, onOpen }) {
  const email = row.user?.email || row.user?.name || "(anonymous)";
  const tone = row.is_hidden ? "border-rose-400/40 bg-rose-500/5" : row.is_flagged ? "border-amber-400/40 bg-amber-500/5" : "";
  return (
    <tr className={`border-b border-ink/5 ${tone} hover:bg-ink/5 cursor-pointer`} onClick={() => onOpen(row)} data-testid={`admin-chat-row-${row.conversation_id}`}>
      <td className="p-3 text-[11px] font-mono text-muted whitespace-nowrap">{fmt(row.last_message_at)}</td>
      <td className="p-3 text-[11px] font-mono uppercase tracking-widest text-ink/80">{row.chat_type}</td>
      <td className="p-3 text-xs text-ink truncate max-w-[180px]">{email}</td>
      <td className="p-3 text-xs text-ink/85 max-w-[460px]">
        <div className="line-clamp-2">{row.last_message_preview || "—"}</div>
        <RedactionTags tags={row.last_message_redactions} />
      </td>
      <td className="p-3 text-[11px] font-mono">
        {row.is_hidden && <span className="text-rose-300 mr-2">HIDDEN</span>}
        {row.is_flagged && <span className="text-amber-soft mr-2">FLAGGED</span>}
        {row.moderation_status && <span className="text-muted">{row.moderation_status}</span>}
      </td>
      <td className="p-3 text-right tabular-nums text-xs text-muted">{row.message_count}</td>
    </tr>
  );
}

function ChatDrawer({ row, onClose, onAction }) {
  const [thread, setThread] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!row) return;
    setLoading(true);
    api.get(`/admin/chats/${encodeURIComponent(row.conversation_id)}?chat_type=${row.chat_type}`)
      .then((r) => setThread(r.data))
      .catch(() => setThread(null))
      .finally(() => setLoading(false));
  }, [row]);

  if (!row) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="admin-chat-drawer" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-full sm:w-[640px] h-full bg-bg border-l border-ink/10 overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-bg/95 backdrop-blur p-4 border-b border-ink/10 flex items-center justify-between">
          <div className="min-w-0">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{row.chat_type}</div>
            <div className="text-sm font-mono text-ink truncate" data-testid="admin-chat-drawer-id">{row.conversation_id}</div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => onAction("flag", row)} className="btn-ghost text-xs" data-testid="admin-chat-flag-btn">Flag</button>
            <button onClick={() => onAction("hide", row)} className="btn-ghost text-xs" data-testid="admin-chat-hide-btn">{row.is_hidden ? "Unhide" : "Hide"}</button>
            <button onClick={onClose} className="btn-ghost text-xs">✕</button>
          </div>
        </div>
        <div className="p-4 space-y-3">
          {loading && <div className="text-muted text-sm font-mono">loading…</div>}
          {!loading && thread && (
            <>
              <div className="brutal-card p-3 text-[11px] font-mono text-muted">
                {thread.user && <div>user · {thread.user.email || thread.user.name || thread.user.user_id || "(anonymous)"}</div>}
                {thread.clone && <div>clone · {thread.clone.display_name} ({thread.clone.slug})</div>}
                {thread.room_slug && <div>room · {thread.room_slug}</div>}
                {thread.debate_id && <div>debate · {thread.debate_id} · side {thread.side} · AI {thread.ai_score}</div>}
                {thread.mode && <div>mode · {thread.mode} · tone {thread.tone}</div>}
              </div>
              <div className="space-y-2">
                {(thread.thread || []).map((m, i) => {
                  const isUser = (m.role || "").startsWith("user");
                  return (
                    <div key={i} className={`brutal-card p-3 ${isUser ? "" : "bg-violet-500/5 border-violet-400/20"}`} data-testid={`admin-chat-msg-${i}`}>
                      <div className="flex items-center justify-between text-[10px] font-mono text-muted mb-1">
                        <span>{m.role}</span>
                        <span>{fmt(m.created_at)}</span>
                      </div>
                      <div className="text-sm text-ink/90 whitespace-pre-wrap break-words">{m.text || "—"}</div>
                      <RedactionTags tags={m.redacted} />
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AdminChats() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [chatType, setChatType] = useState("all");
  const [search, setSearch] = useState("");
  const [days, setDays] = useState(14);
  const [safety, setSafety] = useState("all");
  const [drawer, setDrawer] = useState(null);
  const [exporting, setExporting] = useState(false);

  const fetchOnce = useCallback(async () => {
    try {
      const params = new URLSearchParams({ days: String(days), chat_type: chatType, limit: "120", safety });
      if (search.trim()) params.set("search", search.trim());
      const r = await api.get(`/admin/chats?${params.toString()}`);
      setData(r.data);
      setForbidden(false);
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
    } finally {
      setLoading(false);
    }
  }, [days, chatType, search, safety]);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/chats"); return; }
    if (!user) return;
    setLoading(true);
    const t = setTimeout(fetchOnce, 250); // debounce search
    return () => clearTimeout(t);
  }, [user, authLoading, navigate, fetchOnce]);

  const onAction = async (action, row) => {
    try {
      if (action === "flag") {
        const reason = window.prompt("Flag reason (optional):") || "";
        await api.patch(`/admin/chats/${encodeURIComponent(row.conversation_id)}/flag`, { chat_type: row.chat_type, reason });
        toast.success("Flagged");
      } else if (action === "hide") {
        const hide = !row.is_hidden;
        const reason = hide ? (window.prompt("Hide reason (optional):") || "") : "";
        await api.patch(`/admin/chats/${encodeURIComponent(row.conversation_id)}/hide`, { chat_type: row.chat_type, hide, reason });
        toast.success(hide ? "Hidden" : "Restored");
      }
      setDrawer(null);
      fetchOnce();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Action failed");
    }
  };

  const exportJson = async () => {
    setExporting(true);
    try {
      const r = await api.get(`/admin/chats/export/all?days=${days}&chat_type=${chatType}`);
      const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `chats_${chatType}_${days}d_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;
  if (forbidden) return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="max-w-3xl mx-auto px-4 py-10">
        <div className="brutal-card p-8 text-center" data-testid="admin-chats-forbidden">
          <h1 className="heading-display text-2xl mb-2">Admin only</h1>
        </div>
      </div>
    </div>
  );

  const rows = data?.chats || [];
  const fallbackRows = data?.fallback_rows || [];
  const diagnostic = data?.diagnostic;
  const dbTotal = diagnostic ? Object.values(diagnostic.db_counts || {}).reduce((a, b) => a + b, 0) : null;
  const usingFallback = rows.length === 0 && fallbackRows.length > 0;
  const displayRows = usingFallback ? fallbackRows : rows;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="admin-chats-page">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-3">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Admin · Chat Monitoring</div>
            <h1 className="heading-display text-3xl sm:text-4xl mt-1">All chats</h1>
            <p className="text-xs text-amber-soft mt-2 max-w-2xl" data-testid="admin-chats-privacy-notice">
              <strong className="font-mono uppercase tracking-widest mr-2">PRIVACY NOTICE</strong>
              Chats may be reviewed by platform administrators for safety, abuse prevention, and service improvement. Sensitive values (emails, phone numbers, API keys, passwords, addresses) are automatically redacted before display. Users are informed of this in the privacy policy.
            </p>
          </div>
          <button onClick={exportJson} disabled={exporting} className="btn-ghost text-xs disabled:opacity-50" data-testid="admin-chats-export">
            {exporting ? "Exporting…" : "Export JSON"}
          </button>
        </div>

        <div className="brutal-card p-3 mb-4 flex flex-wrap items-center gap-2" data-testid="admin-chats-filters">
          <select value={chatType} onChange={(e) => setChatType(e.target.value)} className="input-brutal text-xs px-2 py-1 w-auto" data-testid="admin-chats-type-select">
            {CHAT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          <select value={safety} onChange={(e) => setSafety(e.target.value)} className="input-brutal text-xs px-2 py-1 w-auto" data-testid="admin-chats-safety-select">
            {SAFETY_FILTERS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          {[1, 7, 14, 30].map((d) => (
            <button key={d} onClick={() => setDays(d)} className={`px-2.5 py-1 rounded-full text-[11px] font-mono uppercase tracking-widest border ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`admin-chats-days-${d}`}>
              {d === 1 ? "24h" : `${d}d`}
            </button>
          ))}
          <input
            type="text"
            placeholder="search text / email / clone…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input-brutal text-xs px-2 py-1 flex-1 min-w-[180px]"
            data-testid="admin-chats-search"
          />
        </div>

        {loading && !data && <div className="text-muted font-mono text-sm">loading…</div>}

        {/* Diagnostic — surfaces real DB state regardless of filter result */}
        {diagnostic && (
          <div className="brutal-card p-3 mb-3 text-[11px] font-mono" data-testid="admin-chats-diagnostic">
            <div className="text-amber uppercase tracking-widest text-[10px] mb-1">Diagnostic</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted">
              <span>db_total: <span className="text-ink/85">{dbTotal}</span></span>
              <span>returned: <span className="text-ink/85">{rows.length}</span></span>
              <span>window: <span className="text-ink/85">{diagnostic.window_days}d</span></span>
              <span className="break-all">cutoff: <span className="text-ink/85">{diagnostic.cutoff_iso}</span></span>
              {diagnostic.db_counts && Object.entries(diagnostic.db_counts).map(([k, v]) => (
                <span key={k}>{k}: <span className="text-ink/85">{v}</span></span>
              ))}
            </div>
          </div>
        )}

        {/* Safe fallback notice */}
        {usingFallback && (
          <div className="brutal-card p-3 mb-3 border-amber/40 bg-amber-500/10 text-[11px] font-mono" data-testid="admin-chats-fallback-notice">
            <strong className="uppercase tracking-widest text-amber mr-2">Fallback view</strong>
            <span className="text-muted">No chats matched filters in this window. Showing latest 50 unfiltered rows so the panel never looks dead.</span>
          </div>
        )}

        {/* Mobile cards (<md) */}
        <div className="md:hidden space-y-3 mb-6" data-testid="admin-chats-cards-mobile">
          {!loading && displayRows.length === 0 && (
            <div className="brutal-card p-6 text-center text-xs text-muted" data-testid="admin-chats-empty-mobile">
              {dbTotal === 0 ? "Database has no chats yet." : "No chats in this window."}
            </div>
          )}
          {displayRows.map((row) => {
            const email = row.user?.email || row.user?.name || "(anonymous)";
            const tone = row.is_hidden ? "border-rose-400/40 bg-rose-500/5" : row.is_flagged ? "border-amber-400/40 bg-amber-500/5" : "";
            return (
              <button
                key={`${row.chat_type}-${row.conversation_id}`}
                onClick={() => setDrawer(row)}
                className={`brutal-card p-3 w-full text-left min-w-0 overflow-hidden ${tone}`}
                data-testid={`admin-chat-card-${row.conversation_id}`}
              >
                <div className="flex items-center justify-between gap-2 mb-1 min-w-0">
                  <span className="text-[11px] font-mono text-muted whitespace-nowrap truncate min-w-0">{fmt(row.last_message_at)}</span>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-ink/80 shrink-0">{row.chat_type}</span>
                </div>
                <div className="text-xs text-ink overflow-hidden text-ellipsis whitespace-nowrap" title={email}>{email}</div>
                <div className="text-[11px] text-ink/75 mt-1 line-clamp-2 break-words">{row.last_message_preview || "—"}</div>
                <div className="flex flex-wrap items-center gap-2 mt-2 text-[10px] font-mono">
                  {row.is_hidden && <span className="text-rose-300">HIDDEN</span>}
                  {row.is_flagged && <span className="text-amber-soft">FLAGGED</span>}
                  {row.moderation_status && <span className="text-muted">{row.moderation_status}</span>}
                  <span className="text-muted ml-auto">{row.message_count} msgs</span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Desktop table (md+) */}
        <div className="brutal-card overflow-x-auto hidden md:block" data-testid="admin-chats-table">
          <table className="w-full text-sm">
            <thead className="text-[11px] font-mono uppercase tracking-widest text-muted whitespace-nowrap">
              <tr className="border-b border-ink/10">
                <th className="text-left p-3">When</th>
                <th className="text-left p-3">Type</th>
                <th className="text-left p-3">User</th>
                <th className="text-left p-3">Last message (redacted)</th>
                <th className="text-left p-3">Safety</th>
                <th className="text-right p-3">Msgs</th>
              </tr>
            </thead>
            <tbody>
              {displayRows.length === 0 && !loading && <tr><td colSpan="6" className="p-6 text-center text-xs text-muted" data-testid="admin-chats-empty">{dbTotal === 0 ? "Database has no chats yet." : "No chats in this window."}</td></tr>}
              {displayRows.map((row) => <ChatRow key={`${row.chat_type}-${row.conversation_id}`} row={row} onOpen={setDrawer} />)}
            </tbody>
          </table>
        </div>

        <div className="text-[10px] font-mono text-muted mt-6">
          {displayRows.length} {usingFallback ? "fallback rows" : "of recent"} · window {days}d ·{" "}
          <Link className="hover:text-ink underline" to="/admin/safety">Safety filter</Link>
          {" · "}
          <Link className="hover:text-ink underline" to="/admin/debates/retention">Debates retention</Link>
        </div>
      </div>

      {drawer && <ChatDrawer row={drawer} onClose={() => setDrawer(null)} onAction={onAction} />}
    </div>
  );
}
