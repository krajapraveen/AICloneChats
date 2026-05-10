/**
 * Delayed-Delivery Emotional Chat composer + scheduled list + inbox.
 * Single page with three tabs: Compose / Scheduled / Inbox.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const CATEGORIES = [
  { id: "future_self", label: "Future self" },
  { id: "apology", label: "Apology" },
  { id: "memory", label: "Memory" },
  { id: "motivation", label: "Motivation" },
  { id: "love", label: "Love" },
  { id: "grief", label: "Grief" },
  { id: "custom", label: "Custom" },
];

function fmtTime(iso) { try { return new Date(iso).toLocaleString(); } catch { return iso; } }

function StatusBadge({ status }) {
  const map = {
    scheduled: "border-amber/40 text-amber bg-amber-500/10",
    queued: "border-violet-400/40 text-violet-300 bg-violet-500/10",
    delivered: "border-emerald/40 text-emerald-soft bg-emerald-500/10",
    failed: "border-red-400/40 text-red-300 bg-red-500/10",
    cancelled: "border-ink/20 text-muted bg-ink/5",
  };
  return <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${map[status] || ""}`} data-testid={`dm-status-${status}`}>{status}</span>;
}

function Composer({ onCreated, status }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [category, setCategory] = useState("future_self");
  const [recipientType, setRecipientType] = useState("self");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [recipientUserId, setRecipientUserId] = useState("");
  const [deliveryChannel, setDeliveryChannel] = useState("in_app");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [crisis, setCrisis] = useState("");

  const submit = async () => {
    if (!title.trim() || !body.trim()) { toast.error("Title and message required"); return; }
    if (!deliveryDate) { toast.error("Pick a delivery date/time"); return; }
    const dt = new Date(deliveryDate);
    if (isNaN(dt.getTime()) || dt.getTime() <= Date.now() + 30000) { toast.error("Delivery time must be at least 30s in the future"); return; }
    if (recipientType === "email" && !recipientEmail.trim()) { toast.error("Recipient email required"); return; }
    if (recipientType === "clone_user" && !recipientUserId.trim()) { toast.error("Recipient user_id required"); return; }
    setSubmitting(true); setCrisis("");
    try {
      const r = await api.post("/delayed-messages", {
        title: title.trim(),
        message_body: body.trim(),
        emotional_category: category,
        recipient_type: recipientType,
        recipient_email: recipientType === "email" ? recipientEmail.trim() : undefined,
        recipient_user_id: recipientType === "clone_user" ? recipientUserId.trim() : undefined,
        delivery_time: dt.toISOString(),
        delivery_channel: deliveryChannel,
      });
      if (r.data?.blocked && r.data?.self_harm_detected) {
        setCrisis(r.data.crisis_response || "Please reach out to someone you trust.");
      } else {
        toast.success("Scheduled");
        setTitle(""); setBody(""); setDeliveryDate("");
        onCreated?.();
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not schedule");
    } finally {
      setSubmitting(false);
    }
  };

  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 16);

  return (
    <div className="brutal-card p-4 sm:p-5" data-testid="delayed-composer">
      {crisis && (
        <div className="brutal-card p-4 border-amber/40 bg-amber-500/10 mb-4" data-testid="delayed-crisis-response">
          <div className="text-amber font-mono text-xs uppercase tracking-widest mb-2">Pause</div>
          <div className="text-sm whitespace-pre-wrap">{crisis}</div>
        </div>
      )}
      <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (e.g., 'For me, in 90 days')" className="input-brutal w-full text-sm mb-3" maxLength={120} data-testid="delayed-title" />
      <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="What do you want them to read…" rows={5} maxLength={4000} className="input-brutal w-full text-sm mb-3" data-testid="delayed-body" />

      <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Emotional category</div>
      <div className="flex flex-wrap gap-2 mb-3">
        {CATEGORIES.map((c) => (
          <button key={c.id} onClick={() => setCategory(c.id)} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${category === c.id ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`delayed-cat-${c.id}`}>
            {c.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Recipient</div>
          <select value={recipientType} onChange={(e) => setRecipientType(e.target.value)} className="input-brutal text-sm w-full" data-testid="delayed-recipient-type">
            <option value="self">My future self</option>
            <option value="email">Email recipient {!status?.email_configured ? "(disabled — RESEND_API_KEY missing)" : ""}</option>
            <option value="clone_user">Another aiclonechats user</option>
          </select>
          {recipientType === "email" && <input value={recipientEmail} onChange={(e) => setRecipientEmail(e.target.value)} placeholder="recipient@email.com" className="input-brutal text-sm w-full mt-2" data-testid="delayed-recipient-email" />}
          {recipientType === "clone_user" && <input value={recipientUserId} onChange={(e) => setRecipientUserId(e.target.value)} placeholder="recipient user_id" className="input-brutal text-sm w-full mt-2" data-testid="delayed-recipient-user" />}
        </div>
        <div>
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Channel</div>
          <select value={deliveryChannel} onChange={(e) => setDeliveryChannel(e.target.value)} className="input-brutal text-sm w-full" data-testid="delayed-channel">
            <option value="in_app">In-app</option>
            <option value="email" disabled={!status?.email_configured}>Email</option>
            <option value="both" disabled={!status?.email_configured}>Both</option>
          </select>
        </div>
      </div>

      <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Deliver at</div>
      <input type="datetime-local" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} min={new Date(Date.now() + 60000).toISOString().slice(0, 16)} placeholder={tomorrow} className="input-brutal text-sm w-full mb-3" data-testid="delayed-datetime" />

      <button onClick={submit} disabled={submitting} className="btn-brutal w-full disabled:opacity-50" data-testid="delayed-submit">
        {submitting ? "Scheduling…" : "Schedule message →"}
      </button>
    </div>
  );
}

function ScheduledList({ messages, onCancel, onDelete }) {
  if (!messages.length) return <div className="text-muted text-sm font-mono p-3">No scheduled messages.</div>;
  return (
    <div className="space-y-3" data-testid="delayed-scheduled-list">
      {messages.map((m) => (
        <div key={m.delayed_message_id} className="brutal-card p-4" data-testid={`delayed-msg-${m.delayed_message_id}`}>
          <div className="flex items-start justify-between gap-2 mb-1">
            <div className="font-bold text-sm truncate">{m.title}</div>
            <StatusBadge status={m.status} />
          </div>
          <div className="text-xs text-muted font-mono mb-2">
            {m.emotional_category} · {m.delivery_channel} · {m.recipient_type}
          </div>
          <div className="text-sm text-ink/85 whitespace-pre-wrap break-words mb-2 line-clamp-3">{m.message_body}</div>
          <div className="text-[10px] font-mono text-muted">deliver at: {fmtTime(m.delivery_time)}</div>
          {m.failure_reason && <div className="text-[10px] font-mono text-red-300 mt-1">err: {m.failure_reason}</div>}
          <div className="flex gap-2 mt-3">
            {(m.status === "scheduled" || m.status === "queued") && <button onClick={() => onCancel(m.delayed_message_id)} className="btn-ghost text-xs" data-testid={`delayed-cancel-${m.delayed_message_id}`}>Cancel</button>}
            {(m.status !== "delivered" && m.status !== "queued") && <button onClick={() => onDelete(m.delayed_message_id)} className="btn-ghost text-xs text-red-300" data-testid={`delayed-delete-${m.delayed_message_id}`}>Delete</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

function Inbox({ inbox, onOpen }) {
  if (!inbox.length) return <div className="text-muted text-sm font-mono p-3">No delivered messages.</div>;
  return (
    <div className="space-y-3" data-testid="delayed-inbox-list">
      {inbox.map((m) => (
        <div key={m.delayed_message_id} className="brutal-card p-4 cursor-pointer hover:border-ink/40" onClick={() => onOpen(m.delayed_message_id)} data-testid={`delayed-inbox-${m.delayed_message_id}`}>
          <div className="flex items-start justify-between gap-2 mb-1">
            <div className="font-bold text-sm truncate">{m.title}</div>
            {!m.opened_at && <span className="px-2 py-0.5 rounded-full border border-violet-400/40 text-violet-300 bg-violet-500/10 text-[10px] font-mono uppercase tracking-widest">unread</span>}
          </div>
          <div className="text-xs text-muted font-mono mb-2">{m.emotional_category} · delivered {fmtTime(m.delivered_at)}</div>
          <div className="text-sm text-ink/85 whitespace-pre-wrap break-words line-clamp-3">{m.message_body}</div>
        </div>
      ))}
    </div>
  );
}

export default function DelayedChat() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState("compose");
  const [status, setStatus] = useState(null);
  const [messages, setMessages] = useState([]);
  const [inbox, setInbox] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const [s, m, ib] = await Promise.all([
        api.get("/delayed-messages/status"),
        api.get("/delayed-messages"),
        api.get("/delayed-messages/inbox"),
      ]);
      setStatus(s.data);
      setMessages(m.data?.messages || []);
      setInbox(ib.data?.inbox || []);
    } catch (e) {
      if (e?.response?.status === 503) toast.error("Delayed messages disabled for public users.");
    }
  }, []);

  useEffect(() => {
    if (!loading && !user) { navigate("/login?redirect=/delayed-chat"); return; }
    if (user) refresh();
  }, [user, loading, navigate, refresh]);

  const onCancel = async (id) => { try { await api.post(`/delayed-messages/${id}/cancel`); await refresh(); } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); } };
  const onDelete = async (id) => { if (!window.confirm("Delete?")) return; try { await api.delete(`/delayed-messages/${id}`); await refresh(); } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); } };
  const onOpen = async (id) => { try { await api.get(`/delayed-messages/${id}`); await refresh(); } catch { /* noop */ } };

  if (loading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  const featureUnavailable = status && !status.available_for_user;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="delayed-chat-page">
      <Navbar />
      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10">
        <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Lab · Admin/QA preview</div>
        <h1 className="heading-display text-2xl sm:text-3xl mt-1">Delayed emotional chat</h1>
        <p className="text-xs text-muted mt-1 mb-6">Write something now, deliver it later — to your future self, to someone you love, or to someone you owe a sentence to.</p>

        {featureUnavailable && (
          <div className="brutal-card p-4 border-amber/30 bg-amber-500/5" data-testid="delayed-feature-disabled">
            <div className="font-mono text-xs uppercase tracking-widest text-amber">Feature disabled for public</div>
            <div className="text-sm mt-1">Set <code className="text-xs">DELAYED_EMOTIONAL_CHAT_ENABLED=true</code> on the backend to expose this to non-admins.</div>
          </div>
        )}

        {!featureUnavailable && (
          <>
            <div className="flex gap-2 mb-4 border-b border-ink/10 pb-2">
              <button onClick={() => setTab("compose")} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${tab === "compose" ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid="delayed-tab-compose">Compose</button>
              <button onClick={() => setTab("scheduled")} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${tab === "scheduled" ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid="delayed-tab-scheduled">Scheduled ({messages.length})</button>
              <button onClick={() => setTab("inbox")} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${tab === "inbox" ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid="delayed-tab-inbox">Inbox ({inbox.length})</button>
              <Link to="/admin/delayed-messages" className="ml-auto btn-ghost text-xs" data-testid="delayed-admin-link">Admin</Link>
            </div>

            {tab === "compose" && <Composer onCreated={refresh} status={status} />}
            {tab === "scheduled" && <ScheduledList messages={messages} onCancel={onCancel} onDelete={onDelete} />}
            {tab === "inbox" && <Inbox inbox={inbox} onOpen={onOpen} />}
          </>
        )}
      </div>
    </div>
  );
}
