/**
 * Translation Chat — landing page (browse + create rooms).
 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";

const LANGS = [
  { code: "en", name: "English", emoji: "🇬🇧" },
  { code: "hi", name: "Hindi", emoji: "🇮🇳" },
  { code: "te", name: "Telugu", emoji: "🇮🇳" },
  { code: "ja", name: "Japanese", emoji: "🇯🇵" },
];

function getOrCreateDeviceId() {
  const KEY = "tx_device_id";
  let id = window.localStorage.getItem(KEY);
  if (!id) {
    const rand = (typeof crypto !== "undefined" && crypto.randomUUID) ? crypto.randomUUID() : `dev-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    id = `tx-${rand.replace(/-/g, "").slice(0, 24)}`;
    window.localStorage.setItem(KEY, id);
  }
  return id;
}

export default function TranslationChatPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [lang, setLang] = useState(() => window.localStorage.getItem("tx_preferred_lang") || "en");
  const [busy, setBusy] = useState(false);
  const [recentRooms, setRecentRooms] = useState([]);

  useEffect(() => {
    api.post("/analytics/event", { event_name: "translation_chat_opened", metadata: { experience_variant: "translation_v1" } }).catch(() => {});
    const stored = JSON.parse(window.localStorage.getItem("tx_recent_rooms") || "[]");
    setRecentRooms(stored);
  }, []);

  useEffect(() => {
    window.localStorage.setItem("tx_preferred_lang", lang);
  }, [lang]);

  const create = async () => {
    if (!name.trim()) { toast.error("Give your room a name"); return; }
    setBusy(true);
    try {
      const r = await api.post("/translation-chat/rooms", { room_name: name.trim(), preferred_language: lang }, { headers: { "X-Tx-Device-Id": getOrCreateDeviceId() } });
      const room = r.data?.room;
      const updated = [{ room_id: room.room_id, room_name: room.room_name, last_seen: Date.now() }, ...recentRooms.filter(x => x.room_id !== room.room_id)].slice(0, 8);
      window.localStorage.setItem("tx_recent_rooms", JSON.stringify(updated));
      navigate(`/translation-chat/${room.room_id}`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not create room");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="translation-chat-landing">
      <Navbar />
      <div className="max-w-4xl mx-auto px-4 sm:px-5 md:px-8 py-10 sm:py-14">
        <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Real-Time Translation Chat</div>
        <h1 className="heading-display text-3xl sm:text-5xl mt-1 leading-tight">Chat across languages, instantly.</h1>
        <p className="text-sm sm:text-base text-muted mt-3 max-w-2xl leading-relaxed">
          Type in your language. Everyone reads in theirs. AI translates each message in real time across English, Hindi, Telugu, and Japanese.
        </p>

        <div className="brutal-card p-5 sm:p-7 mt-8" data-testid="translation-create-room-card">
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Create a room</div>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Family Group, Friday Hangout, Project Standup"
            maxLength={80}
            className="input-brutal w-full"
            data-testid="translation-create-room-name"
          />
          <div className="mt-4">
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Your language</div>
            <div className="flex flex-wrap gap-2" data-testid="translation-create-language-picker">
              {LANGS.map((L) => (
                <button
                  key={L.code}
                  onClick={() => setLang(L.code)}
                  className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${lang === L.code ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`}
                  data-testid={`translation-lang-${L.code}`}
                >
                  <span className="mr-1">{L.emoji}</span>{L.name}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={create}
            disabled={busy || !name.trim()}
            className="btn-brutal mt-5 w-full sm:w-auto disabled:opacity-50"
            data-testid="translation-create-room-btn"
          >
            {busy ? "Creating…" : "Create room →"}
          </button>
          <p className="text-[10px] font-mono text-muted/80 mt-3 leading-relaxed" data-testid="translation-safety-note">
            Keep messages safe and respectful. Vulgar, sexual, violent, or hateful content is blocked. Translation preserves meaning — it does not censor opinion.
          </p>
        </div>

        {recentRooms.length > 0 && (
          <div className="mt-8" data-testid="translation-recent-rooms">
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-3">Your recent rooms</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {recentRooms.map((r) => (
                <Link key={r.room_id} to={`/translation-chat/${r.room_id}`} className="brutal-card p-4 hover:translate-y-[-2px] transition-transform" data-testid={`translation-recent-${r.room_id}`}>
                  <div className="text-sm font-mono text-ink">{r.room_name}</div>
                  <div className="text-[10px] font-mono text-muted mt-1">{r.room_id}</div>
                </Link>
              ))}
            </div>
          </div>
        )}

        <div className="mt-10 text-[11px] font-mono text-muted">
          Have a room ID? Open it directly: <code className="text-ink/80">/translation-chat/&lt;room_id&gt;</code>
        </div>
      </div>
    </div>
  );
}
