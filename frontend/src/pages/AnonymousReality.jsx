import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";

const ROOM_HUES = {
  loneliness: "rose",
  "family-pressure": "amber",
  "money-reality": "emerald",
  "mental-load": "violet",
  relationships: "rose",
  "startup-struggle": "amber",
  "student-life": "violet",
  "general-reality": "emerald",
};

function hueClass(hue) {
  switch (hue) {
    case "amber": return "tag-amber";
    case "violet": return "tag-violet";
    case "emerald": return "tag-emerald";
    case "rose":
    default: return "tag-rose";
  }
}

export default function AnonymousReality() {
  const [session, setSession] = useState(null);
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function init() {
      try {
        const s = await api.post("/anonymous/session");
        if (cancelled) return;
        setSession(s.data);
        api.post("/anonymous/track", { event_name: "anonymous_page_opened" }).catch(() => {});
        const r = await api.get("/anonymous/rooms");
        if (cancelled) return;
        setRooms(r.data?.rooms || []);
      } catch (e) {
        if (!cancelled) setError("Couldn't reach the rooms. Try again.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    init();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <div className="orb orb-rose w-[420px] h-[420px] -top-20 -right-32 opacity-20 animate-orb" aria-hidden />

      {/* Minimal header — anonymous space, no Navbar contamination */}
      <header className="px-4 sm:px-6 py-4 flex items-center justify-between border-b border-white/5 backdrop-blur-sm sticky top-0 bg-bg/80 z-30 safe-px">
        <Link to="/" className="flex items-center gap-2" data-testid="anon-home-link">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-sm">C</div>
          <span className="font-display font-black text-base text-ink/80">aiclonechats<span className="text-amber">.</span>com</span>
        </Link>
        {session && (
          <div className="flex items-center gap-2" data-testid="anon-handle-badge">
            <span className="text-[10px] font-mono uppercase tracking-widest text-muted">YOU ARE</span>
            <span className="tag tag-rose font-mono">{session.anonymous_handle}</span>
          </div>
        )}
      </header>

      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 sm:py-10 relative" data-testid="anon-reality-page">
        <div className="mb-8">
          <span className="tag tag-rose mb-3 inline-block">ANONYMOUS · MODERATED · NO SOCIAL</span>
          <h1 className="heading-display text-3xl sm:text-4xl md:text-5xl leading-tight mb-3">
            Talk honestly. No names. No fake flexing.
          </h1>
          <p className="text-sm sm:text-base font-medium text-ink/70 leading-relaxed max-w-2xl">
            Topic rooms where strangers say what they actually feel. AI moderation runs before any message goes public. No likes, no followers, no leaderboards — honesty is the only currency here.
          </p>
        </div>

        {loading ? (
          <div className="text-muted font-mono text-sm">opening rooms…</div>
        ) : error ? (
          <div className="brutal-card p-6 text-center" data-testid="anon-error">
            <p className="text-sm text-rose-300 mb-3">{error}</p>
            <button onClick={() => window.location.reload()} className="btn-brutal text-sm">Try again</button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 sm:gap-4" data-testid="anon-rooms-grid">
              {rooms.map((r) => {
                const hue = ROOM_HUES[r.slug] || "rose";
                return (
                  <Link
                    key={r.slug}
                    to={`/anonymous-reality/${r.slug}`}
                    className="brutal-card p-5 sm:p-6 group hover:translate-y-[-2px] transition-transform"
                    data-testid={`anon-room-card-${r.slug}`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                      <span className={`tag ${hueClass(hue)}`}>{r.title.toUpperCase()}</span>
                      <div className="flex items-center gap-1.5">
                        <span className={`inline-block w-2 h-2 rounded-full ${r.active_count > 0 ? "bg-emerald animate-pulse" : "bg-white/20"}`} />
                        <span className="text-[11px] font-mono text-muted">{r.active_count} {r.active_count === 1 ? "person" : "people"}</span>
                      </div>
                    </div>
                    <h3 className="heading-display text-xl mb-2 leading-tight">{r.title}</h3>
                    <p className="text-sm font-medium text-ink/70 leading-relaxed mb-3">{r.description}</p>
                    {r.last_message_preview && (
                      <p className="text-xs text-ink/55 italic line-clamp-2 border-l-2 border-white/10 pl-2" data-testid={`anon-room-preview-${r.slug}`}>
                        "{r.last_message_preview}"
                      </p>
                    )}
                    {r.status === "frozen" && (
                      <span className="text-[10px] font-mono uppercase tracking-widest text-amber-soft mt-3 inline-block" data-testid={`anon-room-frozen-${r.slug}`}>
                        FROZEN · READ-ONLY
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>

            <div className="mt-8 brutal-card p-5 sm:p-6 text-center">
              <h2 className="heading-display text-lg sm:text-xl mb-2">House rules</h2>
              <ul className="text-xs sm:text-sm text-ink/70 space-y-1.5 max-w-xl mx-auto">
                <li>● Speak from your life, not from a hot take.</li>
                <li>● No names. Yours or anyone else's.</li>
                <li>● Toxicity, harassment, doxxing, and spam are blocked before they're shown.</li>
                <li>● If you're struggling, you're welcome here. We won't shame anyone.</li>
                <li>● Report anything that doesn't feel right.</li>
              </ul>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
