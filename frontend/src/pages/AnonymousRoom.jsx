import { useEffect, useMemo, useRef, useState, memo } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import useAnonymousChat from "../hooks/useAnonymousChat";

function relativeTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m`;
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}h`;
  return `${Math.floor(diff / 86400_000)}d`;
}

function StatusPill({ status, mode, activeCount }) {
  const map = {
    connecting: { label: "Connecting…", cls: "bg-amber-500/15 text-amber-soft", dot: "bg-amber animate-pulse" },
    live: { label: "Live", cls: "bg-emerald-500/15 text-emerald-soft", dot: "bg-emerald" },
    polling: { label: "Polling", cls: "bg-violet-500/15 text-violet-soft", dot: "bg-violet" },
    offline: { label: "Offline", cls: "bg-rose-500/15 text-rose-300", dot: "bg-rose" },
    frozen: { label: "Frozen", cls: "bg-rose-500/15 text-rose-300", dot: "bg-rose" },
  };
  const s = map[status] || map.connecting;
  return (
    <div className={`inline-flex items-center gap-2 px-2 py-1 rounded-full text-[10px] font-mono uppercase tracking-widest ${s.cls}`} data-testid="anon-status-pill">
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {s.label}
      <span className="text-muted">·</span>
      <span>{activeCount} here</span>
    </div>
  );
}

function MessageBubbleImpl({ msg, mySessionId, onReport }) {
  const isMine = msg.session_id && msg.session_id === mySessionId;
  const isSystem = msg.message_type === "system";
  const isSeed = msg.message_type === "seed";
  if (isSystem) {
    return (
      <div className="my-2 px-4 py-3 rounded-xl bg-violet-500/10 border border-violet-400/20 text-sm text-violet-soft text-center max-w-md mx-auto" data-testid={`anon-msg-system-${msg.message_id}`}>
        {msg.content}
      </div>
    );
  }
  return (
    <div className={`flex ${isMine ? "justify-end" : "justify-start"} my-2 group`} data-testid={`anon-msg-${msg.message_id}`}>
      <div className={`max-w-[88%] sm:max-w-[78%] ${isMine ? "items-end" : "items-start"} flex flex-col gap-1`}>
        <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest">
          <span className={isMine ? "text-amber-soft" : isSeed ? "text-muted/70" : "text-rose-300"}>{msg.anonymous_handle}{isSeed ? " · seed" : ""}</span>
          <span className="text-muted">{relativeTime(msg.created_at)}</span>
        </div>
        <div className={`px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words ${isMine ? "bg-amber/15 border border-amber/30 text-ink rounded-tr-sm" : "bg-white/5 border border-white/10 text-ink/90 rounded-tl-sm"}`}>
          {msg.content}
        </div>
        {!isMine && !isSeed && (
          <button
            onClick={() => onReport(msg)}
            className="opacity-0 group-hover:opacity-100 text-[10px] font-mono uppercase tracking-widest text-muted hover:text-rose-300 self-start transition"
            data-testid={`anon-msg-report-${msg.message_id}`}
          >
            ⚐ Report
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Memoized to prevent repaint flicker during polling refresh.
 * Re-renders only when message content / moderation state actually changes,
 * or the viewer's session context changes.
 */
const MessageBubble = memo(MessageBubbleImpl, (prev, next) => {
  if (prev.mySessionId !== next.mySessionId) return false;
  if (prev.onReport !== next.onReport) return false;
  const a = prev.msg, b = next.msg;
  if (a === b) return true;
  return (
    a?.message_id === b?.message_id &&
    a?.content === b?.content &&
    a?.moderation_status === b?.moderation_status &&
    a?.message_type === b?.message_type &&
    a?.anonymous_handle === b?.anonymous_handle &&
    a?.created_at === b?.created_at
  );
});

function ReportModal({ open, onClose, onSubmit, message }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setReason(""); }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/75 backdrop-blur-sm p-0 sm:p-4 safe-px" onClick={onClose} data-testid="anon-report-modal">
      <div className="brutal-card modal-shell w-full sm:max-w-md p-6 rounded-t-3xl sm:rounded-3xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <span className="tag tag-rose">REPORT MESSAGE</span>
          <button onClick={onClose} className="text-muted hover:text-ink text-xl leading-none" data-testid="anon-report-close">×</button>
        </div>
        <p className="text-xs text-muted mb-2">Tell us what's off. Admins read these.</p>
        <div className="text-xs italic text-ink/60 border-l-2 border-white/10 pl-2 mb-3 line-clamp-3">"{message?.content}"</div>
        <textarea
          className="input-brutal w-full min-h-[80px]"
          placeholder="e.g. this feels like harassment / spam / unsafe"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
          data-testid="anon-report-textarea"
        />
        <div className="flex gap-2 mt-4">
          <button
            type="button"
            onClick={async () => { setBusy(true); try { await onSubmit(reason); onClose(); toast.success("Reported. Admins will review."); } finally { setBusy(false); } }}
            disabled={busy || !reason.trim()}
            className="btn-brutal flex-1 text-sm"
            data-testid="anon-report-submit"
          >
            {busy ? "Reporting…" : "Report"}
          </button>
          <button onClick={onClose} className="btn-ghost flex-1 text-sm">Cancel</button>
        </div>
      </div>
    </div>
  );
}

export default function AnonymousRoom() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const [room, setRoom] = useState(null);
  const [session, setSession] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [blockedNotice, setBlockedNotice] = useState("");
  const [reportTarget, setReportTarget] = useState(null);
  const [showRules, setShowRules] = useState(false);

  const { messages, status, mode, activeCount, typingHandles, sendMessage, sendTyping, reportMessage } = useAnonymousChat(slug);
  const scrollRef = useRef(null);
  const lastTypingTsRef = useRef(0);

  // Bootstrap session + room
  useEffect(() => {
    let cancelled = false;
    async function init() {
      try {
        const [s, r] = await Promise.all([
          api.post("/anonymous/session"),
          api.get(`/anonymous/rooms/${slug}`),
        ]);
        if (cancelled) return;
        setSession(s.data);
        setRoom(r.data);
        api.post(`/anonymous/rooms/${slug}/join`).catch(() => {});
        api.post("/anonymous/track", { event_name: "anonymous_room_opened", metadata: { room_slug: slug } }).catch(() => {});
      } catch {
        if (!cancelled) {
          toast.error("Room not found");
          navigate("/anonymous-reality");
        }
      }
    }
    init();
    return () => {
      cancelled = true;
      api.post(`/anonymous/rooms/${slug}/leave`).catch(() => {});
      api.post("/anonymous/track", { event_name: "anonymous_room_abandoned", metadata: { room_slug: slug } }).catch(() => {});
    };
  }, [slug, navigate]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (isNearBottom) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Initial scroll to bottom
  useEffect(() => {
    if (scrollRef.current && messages.length > 0) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room?.slug]);

  const onChange = (e) => {
    setDraft(e.target.value);
    setBlockedNotice("");
    const now = Date.now();
    if (now - lastTypingTsRef.current > 1500) {
      lastTypingTsRef.current = now;
      sendTyping();
    }
  };

  const onSend = async (e) => {
    e?.preventDefault?.();
    if (!draft.trim() || sending || status === "frozen") return;
    setSending(true);
    setBlockedNotice("");
    const text = draft.trim();
    const result = await sendMessage(text);
    if (result?.status === "blocked") {
      setBlockedNotice(result.human_reason || "We blocked this to protect the room.");
      api.post("/anonymous/track", { event_name: "anonymous_message_blocked_seen", metadata: { room_slug: slug, category: result.category } }).catch(() => {});
    } else if (result?.status === "error") {
      toast.error(result.error || "Couldn't send");
    } else {
      setDraft("");
    }
    setSending(false);
  };

  const onReport = async (reason) => {
    if (!reportTarget) return;
    api.post("/anonymous/track", { event_name: "anonymous_message_reported_clicked", metadata: { room_slug: slug } }).catch(() => {});
    await reportMessage(reportTarget.message_id, reason);
  };

  const otherTyping = useMemo(() => typingHandles.filter((h) => h !== session?.anonymous_handle), [typingHandles, session]);

  // Stable callback so memoized MessageBubble doesn't re-render on every parent paint.
  const handleReport = useMemo(() => (msg) => setReportTarget(msg), []);

  const isFrozen = room?.status === "frozen" || status === "frozen";

  return (
    <div className="page-bg min-h-screen min-h-[100dvh] flex flex-col" data-testid="anon-room-page">
      <header className="px-4 sm:px-6 py-3 border-b border-white/5 bg-bg/85 backdrop-blur-sm sticky top-0 z-30 safe-px flex items-center justify-between gap-3">
        <Link to="/anonymous-reality" className="text-xs font-mono text-muted hover:text-ink flex items-center gap-1.5 shrink-0" data-testid="anon-room-back">
          ← Rooms
        </Link>
        <div className="text-center min-w-0 flex-1">
          <div className="font-display font-bold text-sm sm:text-base truncate" data-testid="anon-room-title">{room?.title || "…"}</div>
          <div className="flex items-center justify-center gap-2 mt-0.5">
            <StatusPill status={isFrozen ? "frozen" : status} mode={mode} activeCount={activeCount} />
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {session && <span className="hidden sm:inline tag tag-rose font-mono text-[10px]" data-testid="anon-room-handle">{session.anonymous_handle}</span>}
          <button onClick={() => setShowRules(true)} className="text-xs font-mono text-muted hover:text-ink" data-testid="anon-room-rules-btn">Rules</button>
        </div>
      </header>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 sm:px-5 py-3" data-testid="anon-messages-list">
        <div className="max-w-2xl mx-auto">
          {messages.length === 0 && (
            <div className="text-center text-xs font-mono text-muted py-12">Loading conversation…</div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.message_id} msg={m} mySessionId={session?.session_id} onReport={handleReport} />
          ))}
          {otherTyping.length > 0 && (
            <div className="text-[11px] font-mono text-muted italic px-2 py-1" data-testid="anon-typing-indicator">
              {otherTyping.slice(0, 3).join(", ")} {otherTyping.length === 1 ? "is" : "are"} typing…
            </div>
          )}
        </div>
      </div>

      {/* Composer */}
      <form onSubmit={onSend} className="border-t border-white/5 bg-bg/95 backdrop-blur-sm chat-form-sticky safe-px" data-testid="anon-composer-form">
        {blockedNotice && (
          <div className="px-3 sm:px-5 pt-2 text-xs text-rose-300" data-testid="anon-blocked-notice">{blockedNotice}</div>
        )}
        {isFrozen && (
          <div className="px-3 sm:px-5 pt-2 text-xs text-amber-soft">This room is frozen by an admin. Read-only.</div>
        )}
        <div className="px-3 sm:px-5 py-2.5 max-w-2xl mx-auto flex items-end gap-2">
          <textarea
            value={draft}
            onChange={onChange}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder={isFrozen ? "Room is frozen" : "Talk honestly. No names."}
            className="input-brutal flex-1 min-h-[44px] max-h-32 resize-none"
            rows={1}
            maxLength={1500}
            disabled={sending || isFrozen}
            data-testid="anon-composer-input"
          />
          <button
            type="submit"
            disabled={!draft.trim() || sending || isFrozen}
            className="btn-brutal text-sm shrink-0 disabled:opacity-50"
            data-testid="anon-composer-send"
          >
            {sending ? "…" : "Send"}
          </button>
        </div>
        <div className="text-[10px] font-mono text-muted/80 mt-1.5 px-1" data-testid="anon-composer-safety-note">
          Keep it respectful. Vulgar, sexual, violent, or hateful content is blocked.
        </div>
      </form>

      {/* Rules drawer */}
      {showRules && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/75 backdrop-blur-sm" onClick={() => setShowRules(false)} data-testid="anon-rules-modal">
          <div className="brutal-card modal-shell w-full sm:max-w-md p-6 rounded-t-3xl sm:rounded-3xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <span className="tag tag-rose">ROOM RULES</span>
              <button onClick={() => setShowRules(false)} className="text-muted hover:text-ink text-xl leading-none">×</button>
            </div>
            <h3 className="heading-display text-2xl mb-3">{room?.title}</h3>
            <p className="text-sm text-ink/70 leading-relaxed mb-4">{room?.description}</p>
            <ul className="space-y-2 text-sm text-ink/85">
              {(room?.rules || []).map((r, i) => (
                <li key={i} className="flex items-start gap-2"><span className="text-rose-300 mt-0.5">●</span> {r}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <ReportModal
        open={!!reportTarget}
        message={reportTarget}
        onClose={() => setReportTarget(null)}
        onSubmit={onReport}
      />
    </div>
  );
}
