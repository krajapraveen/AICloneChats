/**
 * Translation Room — main chat surface.
 * - If user not joined: show join form (display name + language)
 * - If joined: show messages + composer + member rail
 * - Messages display in user's preferred language; toggle to see original
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import useTranslationChat from "../hooks/useTranslationChat";

const LANGS = [
  { code: "en", name: "English", emoji: "🇬🇧" },
  { code: "hi", name: "Hindi", emoji: "🇮🇳" },
  { code: "te", name: "Telugu", emoji: "🇮🇳" },
  { code: "ja", name: "Japanese", emoji: "🇯🇵" },
];

function langLabel(code) {
  const L = LANGS.find((x) => x.code === code);
  return L ? `${L.emoji} ${L.name}` : code;
}

function fmtTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch { return ""; }
}

function MessageBubbleImpl({ msg, isMine }) {
  const [showOriginal, setShowOriginal] = useState(false);
  const sameLang = msg.is_same_language;
  const text = showOriginal ? msg.original_text : msg.display_text;
  return (
    <div className={`flex ${isMine ? "justify-end" : "justify-start"}`} data-testid={`tx-msg-${msg.message_id}`}>
      <div className={`max-w-[85%] sm:max-w-[75%] brutal-card px-3 py-2 ${isMine ? "bg-violet-500/10 border-violet-400/30" : ""}`}>
        <div className="flex items-center gap-2 text-[10px] font-mono text-muted mb-1">
          <span className="text-ink/80" data-testid={`tx-msg-sender-${msg.message_id}`}>{msg.sender_name}</span>
          <span>·</span>
          <span>{fmtTime(msg.created_at)}</span>
          <span>·</span>
          <span className="uppercase tracking-widest">{msg.source_language}</span>
        </div>
        <div className="text-sm text-ink/90 whitespace-pre-wrap break-words" data-testid={`tx-msg-text-${msg.message_id}`}>{text || "—"}</div>
        <div className="flex items-center gap-3 mt-1.5">
          {!sameLang && (
            <button
              onClick={() => setShowOriginal((s) => {
                if (!s) {
                  api.post("/analytics/event", { event_name: "translation_original_viewed", metadata: { message_id: msg.message_id, experience_variant: "translation_v1" } }).catch(() => {});
                }
                return !s;
              })}
              className="text-[10px] font-mono uppercase tracking-widest text-amber hover:underline"
              data-testid={`tx-msg-toggle-${msg.message_id}`}
            >
              {showOriginal ? "show translation" : "show original"}
            </button>
          )}
          <button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(text || "");
                toast.success("Copied");
                api.post("/analytics/event", { event_name: "translation_message_copied", metadata: { message_id: msg.message_id, experience_variant: "translation_v1" } }).catch(() => {});
              } catch { toast.error("Copy failed"); }
            }}
            className="text-[10px] font-mono uppercase tracking-widest text-muted hover:text-ink"
            data-testid={`tx-msg-copy-${msg.message_id}`}
          >
            copy
          </button>
        </div>
      </div>
    </div>
  );
}

const MessageBubble = memo(MessageBubbleImpl, (prev, next) => (
  prev.isMine === next.isMine &&
  prev.msg.message_id === next.msg.message_id &&
  prev.msg.display_text === next.msg.display_text &&
  prev.msg.original_text === next.msg.original_text
));

function JoinForm({ onJoin, defaultLang }) {
  const [name, setName] = useState("");
  const [lang, setLang] = useState(defaultLang || "en");
  const [busy, setBusy] = useState(false);
  return (
    <div className="brutal-card p-5 sm:p-6 max-w-lg mx-auto mt-10" data-testid="translation-join-form">
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Join this room</div>
      <h2 className="heading-display text-xl mt-1 mb-4">Pick a name and your language</h2>
      <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} placeholder="Display name" className="input-brutal w-full" data-testid="translation-join-name" />
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted mt-4 mb-2">Your language</div>
      <div className="flex flex-wrap gap-2">
        {LANGS.map((L) => (
          <button key={L.code} onClick={() => setLang(L.code)} className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${lang === L.code ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`} data-testid={`translation-join-lang-${L.code}`}>
            <span className="mr-1">{L.emoji}</span>{L.name}
          </button>
        ))}
      </div>
      <button
        onClick={async () => {
          if (!name.trim()) { toast.error("Display name required"); return; }
          setBusy(true);
          try { await onJoin(name.trim(), lang); } catch (e) { toast.error(e?.response?.data?.detail || "Could not join"); }
          finally { setBusy(false); }
        }}
        disabled={busy}
        className="btn-brutal mt-5 w-full disabled:opacity-50"
        data-testid="translation-join-btn"
      >
        {busy ? "Joining…" : "Join room →"}
      </button>
    </div>
  );
}

export default function TranslationRoom() {
  const { roomId } = useParams();
  const { room, me, members, messages, status, error, join, switchLanguage, send } = useTranslationChat(roomId);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

  // Track open
  useEffect(() => {
    api.post(`/translation-chat/rooms/${roomId}/track`, {
      event_name: "translation_chat_room_opened",
      metadata: { room_id: roomId },
    }).catch(() => {});
  }, [roomId]);

  // Auto-scroll on new message (smooth, doesn't interrupt user scrolling up)
  useEffect(() => {
    if (!scrollRef.current) return;
    const el = scrollRef.current;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const myId = me ? `${me.display_name}` : null;
  const onSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await send(text);
      setDraft("");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not send");
    } finally {
      setSending(false);
    }
  }, [draft, sending, send]);

  const onCopyInvite = async () => {
    const url = `${window.location.origin}/translation-chat/${roomId}`;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Invite link copied");
      api.post("/analytics/event", { event_name: "translation_invite_link_copied", metadata: { room_id: roomId, experience_variant: "translation_v1" } }).catch(() => {});
    } catch { toast.error("Copy failed"); }
  };

  if (status === "loading") {
    return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm" data-testid="tx-loading">loading…</div></div>;
  }

  if (error && !room) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]"><Navbar />
        <div className="max-w-3xl mx-auto px-4 py-10">
          <div className="brutal-card p-8 text-center" data-testid="tx-error">
            <h1 className="heading-display text-2xl mb-2">{error}</h1>
            <Link to="/translation-chat" className="btn-ghost mt-4 inline-block">← Translation Chat</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="translation-room-page">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8 flex flex-col h-[calc(100dvh-72px)]">
        <Link to="/translation-chat" className="text-xs font-mono text-muted hover:text-ink mb-2 inline-block self-start" data-testid="tx-back">← Translation Chat</Link>

        <header className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Room</div>
            <h1 className="heading-display text-xl sm:text-2xl mt-1 truncate" data-testid="tx-room-name">{room?.room_name || roomId}</h1>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {me && (
              <select
                value={me.preferred_language}
                onChange={(e) => switchLanguage(e.target.value)}
                className="input-brutal text-xs px-2 py-1 w-auto"
                data-testid="tx-language-selector"
              >
                {LANGS.map((L) => <option key={L.code} value={L.code}>{L.emoji} {L.name}</option>)}
              </select>
            )}
            <button onClick={onCopyInvite} className="btn-ghost text-xs" data-testid="tx-copy-invite">Copy invite</button>
          </div>
        </header>

        {members.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3 text-[10px] font-mono" data-testid="tx-members">
            {members.map((m) => (
              <span key={m.member_id} className={`px-2 py-0.5 rounded-full border ${m.is_online ? "border-emerald/40 text-emerald-soft bg-emerald-500/10" : "border-ink/15 text-muted"}`}>
                {m.display_name} · {langLabel(m.preferred_language)}{m.is_online ? " · online" : ""}
              </span>
            ))}
          </div>
        )}

        {status === "not-joined" && (
          <JoinForm onJoin={join} defaultLang={window.localStorage.getItem("tx_preferred_lang") || "en"} />
        )}

        {status !== "not-joined" && (
          <>
            <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 brutal-card p-3 sm:p-4" data-testid="tx-messages-list">
              {messages.length === 0 && <div className="text-center text-xs font-mono text-muted py-12">No messages yet — say hi.</div>}
              {messages.map((m) => (
                <MessageBubble key={m.message_id} msg={m} isMine={m.sender_name === myId} />
              ))}
            </div>

            <form
              onSubmit={(e) => { e.preventDefault(); onSend(); }}
              className="mt-3 flex items-end gap-2"
              data-testid="tx-composer"
            >
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); } }}
                placeholder={`Type in ${langLabel(me?.preferred_language || "en")}…`}
                maxLength={800}
                className="input-brutal flex-1 min-h-[44px] max-h-32 resize-none"
                disabled={sending}
                data-testid="tx-composer-input"
              />
              <button type="submit" disabled={!draft.trim() || sending} className="btn-brutal text-sm shrink-0 disabled:opacity-50" data-testid="tx-composer-send">
                {sending ? "…" : "Send"}
              </button>
            </form>
            <div className="text-[10px] font-mono text-muted/80 mt-1 px-1" data-testid="tx-safety-note">
              AI translates each message into every member's language. Vulgar, sexual, violent, or hateful content is blocked.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
