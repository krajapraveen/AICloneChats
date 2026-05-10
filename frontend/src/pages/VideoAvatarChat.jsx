/**
 * Video Avatar Chat — admin/QA-gated.
 * Lets admin send a clone chat message and receive a TTS audio reply (or video when fal.ai key is set).
 * Pipeline status polled every 2s while a message is in queued / generating / rendering.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function StatusPill({ status, error }) {
  const label = error
    ? "fallback (text)"
    : status === "completed"
    ? "ready"
    : status === "failed"
    ? "failed"
    : status === "rendering_video"
    ? "rendering video…"
    : status === "generating_audio"
    ? "voicing…"
    : "queued";
  const color = status === "completed" ? "border-emerald/40 text-emerald-soft bg-emerald-500/10"
    : status === "failed" ? "border-red-400/40 text-red-300 bg-red-500/10"
    : "border-amber/40 text-amber bg-amber-500/10";
  return (
    <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${color}`} data-testid={`avatar-status-${status}`}>{label}</span>
  );
}

function MessageBubble({ msg, onRetry }) {
  const isVideo = !!msg.video_url;
  const isAudio = !!msg.audio_url && !isVideo;
  const isText = !msg.audio_url && !msg.video_url;
  return (
    <div className="flex flex-col gap-2 mb-5" data-testid={`avatar-msg-${msg.message_id}`}>
      <div className="text-right text-[10px] font-mono uppercase tracking-widest text-muted">you</div>
      <div className="brutal-card p-3 self-end max-w-[85%] sm:max-w-[70%] bg-violet-500/10 border-violet-400/30">
        <div className="text-sm whitespace-pre-wrap break-words">{msg.input_text}</div>
      </div>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">clone</div>
      <div className="brutal-card p-3 max-w-[85%] sm:max-w-[80%]">
        <div className="flex items-center gap-2 mb-2">
          <StatusPill status={msg.video_status} error={msg.error_code} />
          {msg.error_code && <span className="text-[10px] text-muted font-mono">{msg.error_code}</span>}
        </div>
        {isVideo && (
          <video controls className="w-full rounded-md mb-2 max-h-[60vh]" data-testid={`avatar-video-${msg.message_id}`}>
            <source src={`${process.env.REACT_APP_BACKEND_URL}${msg.video_url}`} type="video/mp4" />
          </video>
        )}
        {isAudio && (
          <audio controls className="w-full mb-2" data-testid={`avatar-audio-${msg.message_id}`}>
            <source src={`${process.env.REACT_APP_BACKEND_URL}${msg.audio_url}`} type="audio/mpeg" />
          </audio>
        )}
        {isText && msg.video_status !== "queued" && msg.video_status !== "generating_audio" && msg.video_status !== "rendering_video" && (
          <div className="text-[11px] text-muted font-mono italic mb-2">media unavailable — text fallback below</div>
        )}
        <div className="text-sm whitespace-pre-wrap break-words text-ink/90" data-testid={`avatar-transcript-${msg.message_id}`}>{msg.ai_response_text}</div>
        {msg.video_status === "failed" && (
          <button onClick={() => onRetry(msg.message_id)} className="btn-ghost text-xs mt-2" data-testid={`avatar-retry-${msg.message_id}`}>Retry</button>
        )}
      </div>
    </div>
  );
}

export default function VideoAvatarChat() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const [status, setStatus] = useState(null);
  const [cloneSlug, setCloneSlug] = useState(search.get("clone") || "companion");
  const [conversationId, setConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [profiles, setProfiles] = useState([]);
  const [selectedAvatarId, setSelectedAvatarId] = useState("");
  const pollRef = useRef(null);
  const scrollRef = useRef(null);

  const refreshStatus = useCallback(async () => {
    try {
      const r = await api.get("/avatar-chat/status");
      setStatus(r.data);
    } catch { /* noop */ }
  }, []);

  const refreshProfiles = useCallback(async () => {
    try {
      const r = await api.get("/avatar-chat/profiles");
      setProfiles(r.data?.profiles || []);
    } catch { /* noop */ }
  }, []);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/video-avatar-chat"); return; }
    if (!user) return;
    refreshStatus();
    refreshProfiles();
  }, [user, authLoading, navigate, refreshStatus, refreshProfiles]);

  // Poll job status for any in-flight message every 2s
  const refreshJobs = useCallback(async () => {
    if (!messages.length) return;
    const inflight = messages.filter((m) => ["queued", "generating_audio", "rendering_video"].includes(m.video_status));
    if (!inflight.length) return;
    const updates = await Promise.all(
      inflight.map((m) =>
        api.get(`/avatar-chat/job/${m.message_id}`).then((r) => r.data?.message).catch(() => null),
      ),
    );
    setMessages((prev) => {
      const map = new Map(prev.map((m) => [m.message_id, m]));
      for (const u of updates) {
        if (u && u.message_id) map.set(u.message_id, u);
      }
      return Array.from(map.values()).sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
    });
  }, [messages]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(refreshJobs, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [refreshJobs]);

  // Auto-scroll
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    if (near) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const r = await api.post("/avatar-chat/send", {
        clone_id_or_slug: cloneSlug || "companion",
        message: text,
        conversation_id: conversationId,
        avatar_id: selectedAvatarId || undefined,
      });
      const m = r.data?.message;
      const cid = r.data?.conversation_id;
      if (cid) setConversationId(cid);
      if (m) setMessages((prev) => [...prev, m]);
      setDraft("");
      // Track event
      api.post("/analytics/event", { event_name: "avatar_message_submitted", metadata: { clone_id: cloneSlug, experience_variant: "avatar_chat_v1" } }).catch(() => {});
    } catch (e) {
      const code = e?.response?.status;
      if (code === 503) toast.error("Avatar Chat is currently disabled for public users.");
      else toast.error(e?.response?.data?.detail || "Could not send message");
    } finally {
      setSending(false);
    }
  }, [draft, sending, cloneSlug, conversationId, selectedAvatarId]);

  const onRetry = useCallback(async (messageId) => {
    try {
      await api.post(`/avatar-chat/retry/${messageId}`);
      toast.success("Retrying…");
      // Force re-poll
      const r = await api.get(`/avatar-chat/job/${messageId}`).catch(() => null);
      const u = r?.data?.message;
      if (u) setMessages((prev) => prev.map((m) => (m.message_id === messageId ? u : m)));
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Retry failed");
    }
  }, []);

  const featureNotAvailable = status && !status.available_for_user;
  const ttsNote = status && !status.tts_configured ? "TTS not configured (EMERGENT_LLM_KEY missing) — text-only replies." : null;
  const lipsyncNote = status && !status.lipsync_configured ? "Lip-sync not configured (FAL_KEY missing) — audio-only replies." : null;

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="video-avatar-chat-page">
      <Navbar />
      <div className="max-w-4xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8 flex flex-col h-[calc(100dvh-72px)]">
        <header className="mb-3">
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Lab · Admin/QA preview</div>
          <h1 className="heading-display text-2xl sm:text-3xl mt-1">Video Avatar Chat</h1>
          <p className="text-xs text-muted mt-1">Talking-avatar clone replies. Falls back to audio, then text, on any pipeline failure.</p>
        </header>

        {featureNotAvailable && (
          <div className="brutal-card p-4 border-amber/30 bg-amber-500/5" data-testid="avatar-feature-disabled">
            <div className="font-mono text-xs uppercase tracking-widest text-amber">Feature disabled for public</div>
            <div className="text-sm mt-1">Set <code className="text-xs">AVATAR_CHAT_ENABLED=true</code> on the backend to expose this to non-admins.</div>
          </div>
        )}

        {!featureNotAvailable && (
          <>
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <input value={cloneSlug} onChange={(e) => setCloneSlug(e.target.value)} placeholder="clone slug (e.g. companion)" className="input-brutal text-sm w-[200px]" data-testid="avatar-clone-slug" />
              {profiles.length > 0 && (
                <select value={selectedAvatarId} onChange={(e) => setSelectedAvatarId(e.target.value)} className="input-brutal text-sm w-auto" data-testid="avatar-profile-picker">
                  <option value="">Default avatar</option>
                  {profiles.map((p) => (<option key={p.avatar_id} value={p.avatar_id}>{p.avatar_name} {p.is_default ? "★" : ""}</option>))}
                </select>
              )}
              <Link to="/video-avatar-chat/profiles" className="btn-ghost text-xs" data-testid="avatar-manage-profiles">Manage avatars</Link>
              <Link to="/admin/avatar-chat" className="btn-ghost text-xs" data-testid="avatar-admin-link">Admin</Link>
            </div>

            {(ttsNote || lipsyncNote) && (
              <div className="text-[10px] font-mono text-muted/80 mb-3" data-testid="avatar-degrade-notes">
                {ttsNote && <div>· {ttsNote}</div>}
                {lipsyncNote && <div>· {lipsyncNote}</div>}
              </div>
            )}

            <div ref={scrollRef} className="flex-1 overflow-y-auto pr-1" data-testid="avatar-message-list">
              {messages.length === 0 && <div className="text-muted text-sm font-mono">Send a message to see an avatar reply.</div>}
              {messages.map((m) => <MessageBubble key={m.message_id} msg={m} onRetry={onRetry} />)}
            </div>

            <div className="chat-form-sticky pt-3 mt-2 border-t border-ink/10 flex flex-col sm:flex-row gap-2">
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Say something to your clone…" rows={2} className="input-brutal text-sm flex-1" data-testid="avatar-input" maxLength={2000} />
              <button onClick={onSend} disabled={!draft.trim() || sending} className="btn-brutal disabled:opacity-50 self-end sm:self-auto" data-testid="avatar-send-btn">
                {sending ? "Sending…" : "Send →"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
