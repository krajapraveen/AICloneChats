import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import ChatBubble from "../components/ChatBubble";
import MoodSignalPill from "../components/MoodSignalPill";
import InfoIcon from "../components/InfoIcon";
import ChatInfoModal from "../components/ChatInfoModal";
import { useMoodTheme } from "../hooks/useMoodTheme";
import { useAuth } from "../contexts/AuthContext";

const COMPANION_SLUG = "companion";

const MOOD_INFO = {
  id: "mood",
  kicker: "MOOD-BASED CHAT",
  title: "How Mood-Based Chat works",
  body:
    "Mood-Based Chat detects the emotional tone of your messages and adapts the response style — calmer when you're stressed, playful when you're playful, supportive when you're sad. There's no persona — just a companion that meets you where you are.",
  how_to: [
    "Just type — no setup required.",
    "The system reads emotional cues in your text.",
    "Replies adjust tone in real time.",
    "A subtle mood pill appears when confidence is high.",
    "Switch to AI Clone Chat anytime if you want a specific personality.",
  ],
  example: {
    input: "I'm feeling overwhelmed and nothing is working.",
    output: "Let's slow this down. You don't need to solve everything at once. Tell me the one thing causing the most pressure right now.",
  },
  safety:
    "This is not therapy or emergency support. If you mention self-harm or danger, the chat will respond supportively and encourage you to reach a trusted person or local emergency services immediately.",
};

function getOrCreateVisitorId() {
  let id = localStorage.getItem("visitor_id");
  if (!id) {
    id = "v_" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
    localStorage.setItem("visitor_id", id);
  }
  return id;
}

export default function MoodChat() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [showInfo, setShowInfo] = useState(false);
  const visitorId = useRef(getOrCreateVisitorId());
  const scrollRef = useRef(null);
  const { moodUI, theme: moodTheme, updateMoodUI } = useMoodTheme();

  useEffect(() => {
    if (!authLoading && !user) {
      navigate("/login?redirect=/mood-chat");
    }
  }, [authLoading, user, navigate]);

  useEffect(() => {
    api.post("/analytics/event", { event_name: "mood_chat_started" }).catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const send = async (e) => {
    e?.preventDefault?.();
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { sender: "visitor", text, key: Date.now() }]);
    setSending(true);
    try {
      const { data } = await api.post(`/clones/${COMPANION_SLUG}/chat`, {
        message: text,
        visitor_id: visitorId.current,
        conversation_id: conversationId,
      });
      setConversationId(data.conversation_id);
      setMessages((m) => [...m, { sender: "clone", text: data.reply, key: Date.now() + 1 }]);
      if (data.mood_ui) updateMoodUI(data.mood_ui);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Couldn't send");
      setMessages((m) => [...m, { sender: "clone", text: "(I hit a snag. Try again?)", key: Date.now() + 1 }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="page-bg min-h-screen min-h-[100dvh] flex flex-col">
      <Navbar />
      <div className="orb orb-violet w-[420px] h-[420px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-amber w-[380px] h-[380px] top-60 -left-32 opacity-20 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      <div className="max-w-3xl w-full mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8 flex-1 flex flex-col relative" data-testid="mood-chat-page">
        {/* Header */}
        <div className="glass-card p-6 mb-5" data-testid="mood-chat-header">
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 rounded-full bg-gradient-to-br from-violet to-amber flex items-center justify-center font-display font-black text-bg text-2xl shadow-glow-violet">
              ✨
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <h1 className="heading-display text-2xl sm:text-3xl truncate">Mood-Based Chat</h1>
                <span className="tag tag-violet">EMOTION-FIRST</span>
                <InfoIcon onClick={() => setShowInfo(true)} label="How Mood-Based Chat works" testId="info-icon-mood-chat-header" />
              </div>
              <p className="text-sm font-medium text-ink/75 leading-relaxed mt-1">
                No persona, no setup. Type how you feel — the chat tone adapts to match.
              </p>
            </div>
          </div>
        </div>

        {/* Chat */}
        <div
          className="glass-card p-0 flex-1 flex flex-col min-h-[420px] overflow-hidden"
          data-mood-theme={moodUI?.theme || "default"}
          data-mood-state={moodUI?.dominant_state || "neutral"}
          data-testid="mood-chat-container"
        >
          {moodUI?.show_mood_pill && moodUI?.microcopy && (
            <div className="px-5 pt-4 pb-1 flex justify-end">
              <MoodSignalPill moodUI={moodUI} theme={moodTheme} />
            </div>
          )}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 space-y-4" data-testid="mood-chat-scroll">
            {messages.length === 0 && (
              <div className="text-center py-10">
                <p className="font-display text-xl mb-1.5 text-ink">Start typing.</p>
                <p className="text-sm text-muted font-medium max-w-sm mx-auto">
                  No setup, no persona — just an AI companion that adapts to your mood.
                </p>
              </div>
            )}
            {messages.map((m) => (
              <ChatBubble
                key={m.key}
                sender={m.sender}
                text={m.text}
                name={m.sender === "visitor" ? "you" : "Companion"}
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

          <form onSubmit={send} className="border-t border-white/10 p-4 chat-form-sticky flex gap-2" data-testid="mood-chat-form">
            <input
              className="input-brutal flex-1 min-w-0"
              required
              disabled={sending}
              maxLength={2000}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="What's on your mind?"
              data-testid="mood-chat-input"
            />
            <button type="submit" disabled={sending || !input.trim()} className="btn-violet flex-shrink-0" data-testid="mood-chat-send-btn">
              Send
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-muted mt-5 font-mono uppercase tracking-widest">
          Want a specific personality? <Link to="/explore" className="underline hover:text-amber-soft">Browse AI Clones →</Link>
        </p>
      </div>

      <ChatInfoModal open={showInfo} onClose={() => setShowInfo(false)} info={MOOD_INFO} />
    </div>
  );
}
