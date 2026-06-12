import { useEffect, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { toast } from "sonner";
import api from "../../lib/api";
import { useAuth } from "../../contexts/AuthContext";

function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function StatusBadge({ status }) {
  const map = {
    open: { label: "Open", cls: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10" },
    awaiting_user: { label: "Awaiting you", cls: "border-amber/40 text-amber bg-amber/10" },
    resolved: { label: "Resolved", cls: "border-violet/40 text-violet-soft bg-violet/10" },
    closed: { label: "Closed", cls: "border-white/15 text-muted bg-white/[0.03]" },
  };
  const m = map[status] || map.open;
  return <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${m.cls}`}>{m.label}</span>;
}

function NewThreadForm({ onCreated }) {
  const [kind, setKind] = useState("recommendation");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (subject.trim().length < 3 || body.trim().length < 10) {
      toast.error("Please write at least 3 chars for subject and 10 for body.");
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.post("/support/threads", {
        kind, subject: subject.trim(), body: body.trim(),
      });
      toast.success("Sent. We respond within 3 business days.");
      setSubject(""); setBody("");
      onCreated?.(data);
    } catch (err) {
      toast.error(err?.response?.data?.detail?.message || "Could not send.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={submit} className="brutal-card p-5 space-y-3" data-testid="inbox-new-thread-form">
      <div className="text-[10px] font-mono uppercase tracking-widest text-amber">New message to admin</div>
      <div className="flex gap-2 flex-wrap">
        {["recommendation", "concern"].map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setKind(k)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
              kind === k ? "border-amber/60 bg-amber/15 text-amber" : "border-white/10 bg-white/[0.03] text-ink/80 hover:bg-white/[0.07]"
            }`}
            data-testid={`inbox-kind-${k}`}
          >
            {k === "concern" ? "Concern" : "Recommendation"}
          </button>
        ))}
      </div>
      <input
        type="text"
        className="input-brutal"
        placeholder="Subject (3-120 chars)"
        value={subject}
        onChange={(e) => setSubject(e.target.value.slice(0, 120))}
        data-testid="inbox-subject-input"
        maxLength={120}
      />
      <textarea
        className="input-brutal min-h-[140px]"
        placeholder="Write your message (10-4000 chars)"
        value={body}
        onChange={(e) => setBody(e.target.value.slice(0, 4000))}
        data-testid="inbox-body-input"
        maxLength={4000}
      />
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[11px] text-muted">
          Admins will read this here. Their reply appears below — no email is sent.
        </p>
        <button type="submit" disabled={loading} className="btn-brutal text-xs disabled:opacity-50" data-testid="inbox-send-btn">
          {loading ? "Sending…" : "Send"}
        </button>
      </div>
    </form>
  );
}

function ThreadView({ threadId, onBack, onChanged }) {
  const [thread, setThread] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  const load = () => {
    setLoading(true);
    api.get(`/support/threads/${threadId}`)
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
      await api.post(`/support/threads/${threadId}/messages`, { body: reply.trim() });
      setReply("");
      load();
      onChanged?.();
    } catch (err) {
      toast.error(err?.response?.data?.detail?.message || "Could not send.");
    } finally {
      setSending(false);
    }
  };

  if (loading) return <p className="text-sm text-muted">Loading thread…</p>;
  if (!thread) return <p className="text-sm text-rose-soft">Thread not found.</p>;

  return (
    <div data-testid="inbox-thread-view">
      <button type="button" onClick={onBack} className="text-[11px] font-mono uppercase tracking-widest text-amber mb-3 hover:text-amber-soft" data-testid="inbox-thread-back">
        ← Back to inbox
      </button>
      <div className="brutal-card p-5">
        <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
          <div>
            <h3 className="font-display text-xl">{thread.subject}</h3>
            <p className="text-[11px] font-mono uppercase tracking-widest text-muted mt-0.5">
              {thread.kind} · {thread.message_count} message{thread.message_count === 1 ? "" : "s"}
            </p>
          </div>
          <StatusBadge status={thread.status} />
        </div>

        <div className="space-y-3 mb-5 max-h-[420px] overflow-y-auto pr-1">
          {(thread.messages || []).map((m) => (
            <div key={m.message_id}
                 className={`p-3 rounded-lg border ${m.sender === "admin" ? "bg-amber/5 border-amber/30" : "bg-white/[0.03] border-white/10"}`}
                 data-testid={`inbox-msg-${m.message_id}`}>
              <div className="flex items-center justify-between gap-2 text-[10px] font-mono uppercase tracking-widest mb-1">
                <span className={m.sender === "admin" ? "text-amber" : "text-ink/70"}>
                  {m.sender === "admin" ? "ADMIN" : "YOU"} · {m.sender_email}
                </span>
                <span className="text-muted">{formatDate(m.created_at)}</span>
              </div>
              <p className="text-sm whitespace-pre-wrap text-ink/90">{m.body}</p>
            </div>
          ))}
        </div>

        {thread.status === "closed" ? (
          <p className="text-sm text-muted text-center py-3">This thread is closed. Open a new one if you need more help.</p>
        ) : (
          <form onSubmit={send} className="space-y-2" data-testid="inbox-reply-form">
            <textarea
              className="input-brutal min-h-[90px]"
              placeholder="Write a reply…"
              value={reply}
              onChange={(e) => setReply(e.target.value.slice(0, 4000))}
              data-testid="inbox-reply-input"
              maxLength={4000}
            />
            <div className="flex justify-end">
              <button type="submit" disabled={sending || reply.trim().length < 2} className="btn-brutal text-xs disabled:opacity-50" data-testid="inbox-reply-send-btn">
                {sending ? "Sending…" : "Send reply"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

export default function Inbox() {
  const ctx = useOutletContext();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [threads, setThreads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [adminUnread, setAdminUnread] = useState(0);

  const reload = () => {
    setLoading(true);
    api.get("/support/threads")
      .then((r) => setThreads(r.data?.items || []))
      .finally(() => setLoading(false));
    ctx?.refreshUnread?.();
  };

  // Admins-only: pull the count of unread user-submitted threads from the
  // admin support endpoint so they can see at a glance that there are
  // messages waiting for them in a *different* inbox (`/admin/support`).
  // Without this, an admin who lands on their own /account/inbox sees
  // their personal threads (empty) and thinks no user has reached out.
  const loadAdminUnread = () => {
    if (!isAdmin) return;
    api.get("/admin/support/threads?unread_only=true&limit=1")
      .then((r) => setAdminUnread(r.data?.unread_total || 0))
      .catch(() => setAdminUnread(0));
  };

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, []);
  useEffect(() => { loadAdminUnread(); /* eslint-disable-next-line */ }, [isAdmin]);

  if (selectedId) {
    return (
      <Inbox.Wrap>
        <ThreadView threadId={selectedId} onBack={() => { setSelectedId(null); reload(); }} onChanged={reload} />
      </Inbox.Wrap>
    );
  }

  return (
    <Inbox.Wrap>
      {isAdmin && (
        <Link
          to="/admin/support"
          className="block brutal-card p-4 mb-5 border-amber/40 bg-amber/[0.08] hover:bg-amber/[0.14] transition group"
          data-testid="inbox-admin-shortcut"
        >
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-0.5">
                Admin · concerns & recommendations
              </div>
              <div className="text-sm">
                {adminUnread > 0 ? (
                  <>
                    <span className="font-display text-base font-semibold" data-testid="inbox-admin-shortcut-count">{adminUnread}</span>
                    <span className="text-muted"> unread user thread{adminUnread === 1 ? "" : "s"} waiting in <span className="text-amber">Admin Support</span>.</span>
                  </>
                ) : (
                  <span className="text-muted">No unread user threads. Open <span className="text-amber">Admin Support</span> to view history.</span>
                )}
              </div>
              <div className="text-[11px] text-muted/70 mt-1">
                This panel below is YOUR personal inbox (messages you send). User-submitted concerns live in the admin panel.
              </div>
            </div>
            <span className="font-mono text-[11px] uppercase tracking-widest text-amber group-hover:translate-x-0.5 transition shrink-0">
              Open →
            </span>
          </div>
        </Link>
      )}

      <NewThreadForm onCreated={() => reload()} />

      <h3 className="heading-display text-lg mt-8 mb-3">Your messages</h3>
      {loading && <p className="text-sm text-muted">Loading…</p>}
      {!loading && threads.length === 0 && (
        <p className="text-sm text-muted" data-testid="inbox-empty">No messages yet. Send your first concern or recommendation above.</p>
      )}
      {!loading && threads.length > 0 && (
        <div className="space-y-2" data-testid="inbox-threads-list">
          {threads.map((t) => (
            <button
              key={t.thread_id}
              type="button"
              onClick={() => setSelectedId(t.thread_id)}
              className="w-full text-left brutal-card p-4 hover:bg-white/[0.06] transition flex items-center justify-between gap-4"
              data-testid={`inbox-thread-${t.thread_id}`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="font-display text-base truncate">{t.subject}</span>
                  {t.unread_for_user && (
                    <span className="px-1.5 py-0.5 rounded-full bg-amber text-black text-[9px] font-bold uppercase tracking-widest" data-testid={`inbox-thread-${t.thread_id}-unread`}>NEW</span>
                  )}
                </div>
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted">
                  {t.kind} · {t.message_count} msg · {formatDate(t.last_message_at)}
                </div>
              </div>
              <StatusBadge status={t.status} />
            </button>
          ))}
        </div>
      )}
    </Inbox.Wrap>
  );
}

Inbox.Wrap = function Wrap({ children }) {
  return (
    <section data-testid="inbox-section">
      <h2 className="heading-display text-2xl mb-1">Inbox</h2>
      <p className="text-sm text-muted mb-5">Direct line to the admin team. Everything stays in-app — admins read it here and reply here. No email ping.</p>
      {children}
    </section>
  );
};
