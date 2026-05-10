import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { formatCount } from "../lib/format";
import MarqueeDisclaimer from "../components/MarqueeDisclaimer";
import ChatBubble from "../components/ChatBubble";
import ShareCardModal from "../components/ShareCardModal";
import MoodSignalPill from "../components/MoodSignalPill";
import ConversationArtifactsPanel from "../components/ConversationArtifactsPanel";
import { useMoodTheme } from "../hooks/useMoodTheme";

function getOrCreateVisitorId() {
  let id = localStorage.getItem("visitor_id");
  if (!id) {
    id = "v_" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
    localStorage.setItem("visitor_id", id);
  }
  return id;
}

const SHARE_WORTHY_THRESHOLD = 80;

export default function PublicClone() {
  const { slug } = useParams();
  const [clone, setClone] = useState(null);
  const [stats, setStats] = useState({ share_count: 0, message_count: 0, visitor_count: 0 });
  const [error, setError] = useState("");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [visitorName, setVisitorName] = useState(localStorage.getItem("visitor_name") || "");
  const [showNamePrompt, setShowNamePrompt] = useState(!localStorage.getItem("visitor_name"));
  const [shareTarget, setShareTarget] = useState(null); // { reply, question }
  const visitorId = useRef(getOrCreateVisitorId());
  const scrollRef = useRef(null);
  const { moodUI, theme: moodTheme, updateMoodUI } = useMoodTheme();

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get(`/clones/by-slug/${slug}`);
        setClone(data);
        api.get(`/analytics/stats/${slug}`).then((r) => setStats(r.data)).catch(() => {});
      } catch (e) {
        setError(e?.response?.data?.detail || "Clone not found");
      }
    })();
  }, [slug]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const send = async (e) => {
    e?.preventDefault?.();
    const text = input.trim();
    if (!text || !clone || sending) return;
    setInput("");
    const newMsg = { sender: "visitor", text, key: Date.now() };
    setMessages((m) => [...m, newMsg]);
    setSending(true);
    try {
      const { data } = await api.post(`/clones/${clone.slug}/chat`, {
        message: text,
        visitor_id: visitorId.current,
        visitor_name: visitorName || null,
        conversation_id: conversationId,
      });
      setConversationId(data.conversation_id);
      setMessages((m) => [...m, { sender: "clone", text: data.reply, key: Date.now() + 1, prevQuestion: text }]);
      if (data.mood_ui) updateMoodUI(data.mood_ui);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Couldn't send");
      setMessages((m) => [...m, { sender: "clone", text: "(Hmm, I couldn't reply just now. Try again?)", key: Date.now() + 1, prevQuestion: text }]);
    } finally {
      setSending(false);
    }
  };

  const submitName = (e) => {
    e.preventDefault();
    if (visitorName.trim()) {
      localStorage.setItem("visitor_name", visitorName.trim());
      setShowNamePrompt(false);
    }
  };

  const copyShare = () => {
    navigator.clipboard.writeText(window.location.href);
    toast.success("Share link copied!");
    api.post("/analytics/event", { event_name: "clone_shared", clone_id: clone?.clone_id, metadata: { channel: "link" } }).catch(() => {});
  };

  const openShareCard = (msg) => {
    setShareTarget({ reply: msg.text, question: msg.prevQuestion });
    // optimistic share count bump
    setStats((s) => ({ ...s, share_count: (s.share_count || 0) + 1 }));
  };

  if (error) {
    return (
      <div className="page-bg flex items-center justify-center px-5 min-h-screen">
        <div className="orb orb-violet w-[400px] h-[400px] top-1/4 left-1/4 animate-orb" aria-hidden />
        <div className="glass-card p-10 text-center max-w-md relative" data-testid="clone-not-found">
          <h1 className="heading-display text-3xl mb-2">404 — no clone here</h1>
          <p className="text-muted font-medium">{error}</p>
        </div>
      </div>
    );
  }

  if (!clone) {
    return <div className="page-bg flex items-center justify-center font-display min-h-screen text-ink">Loading clone…</div>;
  }

  const avatarSrc = clone.avatar_url
    ? (clone.avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${clone.avatar_url}` : clone.avatar_url)
    : null;

  return (
    <div className="page-bg flex flex-col min-h-screen min-h-[100dvh]">
      <MarqueeDisclaimer />

      <div className="orb orb-amber w-[380px] h-[380px] top-20 -right-20 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[420px] h-[420px] bottom-10 -left-32 opacity-25 animate-orb" style={{ animationDelay: "3s" }} aria-hidden />

      <div className="max-w-3xl w-full mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8 flex-1 flex flex-col" data-testid="public-clone-page">
        {/* Header */}
        <div className="glass-card p-6 mb-5" data-testid="clone-header">
          <div className="flex items-start gap-4">
            {avatarSrc ? (
              <img src={avatarSrc} alt={clone.display_name} className="w-20 h-20 rounded-full border border-white/15 object-cover shadow-glow-amber" />
            ) : (
              <div className="w-20 h-20 rounded-full bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-3xl shadow-glow-amber">
                {clone.display_name?.[0]?.toUpperCase()}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <h1 className="heading-display text-3xl truncate">{clone.display_name}</h1>
                <span className="tag tag-amber">AI CLONE</span>
                {stats.share_count > 0 && (
                  <span className="tag tag-violet" data-testid="header-share-counter" title={`${stats.share_count} shares`}>
                    ✨ {formatCount(stats.share_count)} shares
                  </span>
                )}
              </div>
              <p className="font-mono text-xs text-muted">aiclonechats.com/{clone.slug}</p>
              {clone.bio && <p className="mt-2 text-sm font-medium text-ink/80 leading-relaxed">{clone.bio}</p>}
              {(stats.message_count > 0 || stats.visitor_count > 0) && (
                <div className="mt-3 flex items-center gap-4 text-[11px] font-mono uppercase tracking-wider text-muted" data-testid="header-stats">
                  <span>💬 {formatCount(stats.message_count)} chats</span>
                  <span>● {formatCount(stats.visitor_count)} visitors</span>
                </div>
              )}
            </div>
            <button onClick={copyShare} className="btn-ghost text-xs hidden md:inline-flex" data-testid="share-btn">Share ↗</button>
          </div>
        </div>

        {/* Chat */}
        <div
          className="glass-card p-0 flex-1 flex flex-col min-h-[400px] overflow-hidden"
          data-mood-theme={moodUI?.theme || "default"}
          data-mood-state={moodUI?.dominant_state || "neutral"}
          data-testid="chat-container"
        >
          {moodUI?.show_mood_pill && moodUI?.microcopy && (
            <div className="px-5 pt-4 pb-1 flex justify-end" data-testid="mood-pill-wrap">
              <MoodSignalPill moodUI={moodUI} theme={moodTheme} />
            </div>
          )}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 space-y-4" data-testid="chat-scroll">
            {messages.length === 0 && (
              <div className="text-center py-10">
                <p className="font-display text-xl mb-1.5 text-ink">Say hi to {clone.display_name}.</p>
                <p className="text-sm text-muted font-medium">This is an AI clone — not the real {clone.display_name}.</p>
              </div>
            )}
            {messages.map((m) => (
              <ChatBubble
                key={m.key}
                sender={m.sender}
                text={m.text}
                name={m.sender === "visitor" ? (visitorName || "you") : clone.display_name}
                onShare={m.sender === "clone" ? () => openShareCard(m) : undefined}
                shareWorthy={m.sender === "clone" && (m.text || "").length >= SHARE_WORTHY_THRESHOLD}
              />
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="chat-bubble-clone">
                  <span className="dot-typing" />
                  <span className="dot-typing" />
                  <span className="dot-typing" />
                </div>
              </div>
            )}
          </div>

          {showNamePrompt ? (
            <form onSubmit={submitName} className="border-t border-white/10 p-4 chat-form-sticky flex flex-col sm:flex-row gap-2 bg-amber/5" data-testid="visitor-name-form">
              <input className="input-brutal flex-1 min-w-0" required maxLength={40} value={visitorName} onChange={(e) => setVisitorName(e.target.value)} placeholder="What should they call you? (e.g. Sam)" data-testid="visitor-name-input" />
              <button type="submit" className="btn-brutal flex-shrink-0" data-testid="visitor-name-submit">Start chatting →</button>
            </form>
          ) : (
            <form onSubmit={send} className="border-t border-white/10 p-4 chat-form-sticky flex gap-2" data-testid="chat-form">
              <input className="input-brutal flex-1 min-w-0" required disabled={sending} maxLength={2000} value={input} onChange={(e) => setInput(e.target.value)} placeholder={`Message ${clone.display_name}…`} data-testid="chat-input" />
              <button type="submit" disabled={sending || !input.trim()} className="btn-brutal flex-shrink-0" data-testid="chat-send-btn">Send</button>
            </form>
          )}
        </div>

        <p className="text-center text-xs text-muted mt-5 font-mono uppercase tracking-widest">
          Built on aiclonechats.com · <a href="/" className="underline hover:text-amber-soft">Make your own →</a>
        </p>

        {/* Conversation Artifacts — pull-only, no nudges */}
        <ConversationArtifactsPanel conversationId={conversationId} visitorId={visitorId.current} />
      </div>

      <ShareCardModal
        open={!!shareTarget}
        onClose={() => setShareTarget(null)}
        clone={clone}
        message={shareTarget?.reply || ""}
        visitorMessage={shareTarget?.question}
      />
    </div>
  );
}
