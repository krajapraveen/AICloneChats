import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { formatCount, MOOD_META } from "../lib/format";
import Navbar from "../components/Navbar";

const CATEGORIES = [
  { id: "trending", label: "Most Shared", emoji: "🔥" },
  { id: "funny", label: "Funniest", emoji: "😂" },
  { id: "deep", label: "Deep", emoji: "🧠" },
  { id: "savage", label: "Savage", emoji: "💀" },
  { id: "quote", label: "Quotable", emoji: "✨" },
  { id: "active", label: "Most Active", emoji: "💬" },
  { id: "recent", label: "New", emoji: "🆕" },
];

function CloneTile({ c }) {
  const avatarSrc = c.avatar_url
    ? (c.avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${c.avatar_url}` : c.avatar_url)
    : null;
  const mood = c.primary_mood && MOOD_META[c.primary_mood];

  return (
    <Link to={`/${c.slug}`} className="brutal-card p-5 flex flex-col group" data-testid={`explore-card-${c.slug}`}>
      <div className="flex items-start gap-3 mb-3">
        {avatarSrc ? (
          <img src={avatarSrc} alt={c.display_name} className="w-14 h-14 rounded-full border border-white/15 object-cover" />
        ) : (
          <div className="w-14 h-14 rounded-full bg-gradient-to-br from-violet to-amber flex items-center justify-center font-display font-black text-bg text-xl">
            {c.display_name?.[0]?.toUpperCase() || "C"}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h3 className="font-display font-bold text-lg truncate text-ink leading-tight">{c.display_name}</h3>
          <p className="font-mono text-[11px] text-muted truncate">/{c.slug}</p>
        </div>
        {mood && (
          <span className={`tag ${mood.color} flex-shrink-0`} title={`Mostly shared as ${mood.label}`}>
            {mood.emoji}
          </span>
        )}
      </div>

      {c.bio && <p className="text-sm font-medium text-ink/65 line-clamp-2 mb-4 flex-1">{c.bio}</p>}

      <div className="flex items-center justify-between gap-3 text-[11px] font-mono uppercase tracking-wider text-muted mt-auto">
        <span className="flex items-center gap-1.5" title={`${c.share_count} shares`}>
          <span className="text-amber-soft">✨</span> {formatCount(c.share_count)}
        </span>
        <span className="flex items-center gap-1.5" title={`${c.message_count} messages`}>
          <span className="text-violet-soft">💬</span> {formatCount(c.message_count)}
        </span>
        <span className="flex items-center gap-1.5" title={`${c.visitor_count} unique visitors`}>
          <span className="text-emerald">●</span> {formatCount(c.visitor_count)}
        </span>
      </div>
    </Link>
  );
}

export default function Explore() {
  const [category, setCategory] = useState("trending");
  const [clones, setClones] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const { data } = await api.get(`/explore?category=${category}&limit=30`);
        if (!cancelled) setClones(data.clones || []);
      } catch {
        if (!cancelled) setClones([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [category]);

  const active = CATEGORIES.find((c) => c.id === category) || CATEGORIES[0];

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[420px] h-[420px] -top-20 -right-20 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[460px] h-[460px] top-40 -left-32 opacity-25 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      <div className="max-w-6xl mx-auto px-5 md:px-8 py-12 relative" data-testid="explore-page">
        <div className="max-w-2xl mb-8">
          <p className="font-mono text-xs uppercase tracking-widest text-muted mb-3">DISCOVERY · CLONE WORLD</p>
          <h1 className="heading-display text-4xl md:text-6xl mb-3">
            Talk to <span className="bg-gradient-to-r from-amber to-violet-soft bg-clip-text text-transparent">strangers' AI</span>.
          </h1>
          <p className="text-muted font-medium leading-relaxed">
            Real people built these clones. Their humor, their takes, their weird opinions — encoded in conversation. Pick one. Start typing.
          </p>
        </div>

        {/* Category pills */}
        <div className="flex flex-wrap gap-2 mb-8" data-testid="explore-categories">
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setCategory(cat.id)}
              className={`tag transition ${category === cat.id ? "tag-amber border-amber/55 text-amber-soft scale-105" : "hover:border-white/25"}`}
              data-testid={`explore-cat-${cat.id}`}
            >
              {cat.emoji} {cat.label}
            </button>
          ))}
        </div>

        {/* Section header */}
        <div className="flex items-end justify-between mb-5">
          <h2 className="heading-display text-2xl md:text-3xl">{active.emoji} {active.label}</h2>
          <span className="font-mono text-xs text-muted">{clones.length} {clones.length === 1 ? "clone" : "clones"}</span>
        </div>

        {/* Grid */}
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="brutal-card p-5 h-[180px] animate-pulse opacity-50" />
            ))}
          </div>
        ) : clones.length === 0 ? (
          <div className="glass-card p-10 text-center" data-testid="explore-empty">
            <div className="text-4xl mb-3">🌱</div>
            <h3 className="heading-display text-2xl mb-2">Nothing here yet.</h3>
            <p className="text-muted font-medium mb-5">Be the first {active.label.toLowerCase()} clone — your share counter starts at 0 too.</p>
            <Link to="/register" className="btn-brutal" data-testid="explore-create-cta">Build a clone →</Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" data-testid="explore-grid">
            {clones.map((c) => <CloneTile key={c.clone_id} c={c} />)}
          </div>
        )}

        <div className="mt-16 glass-card p-6 md:p-8 flex flex-col md:flex-row items-start md:items-center justify-between gap-4" data-testid="explore-bottom-cta">
          <div>
            <h3 className="heading-display text-xl md:text-2xl mb-1">Want to be on this page?</h3>
            <p className="text-sm text-muted font-medium">Make your clone, share the link, climb the list.</p>
          </div>
          <Link to="/register" className="btn-brutal flex-shrink-0">Build your clone →</Link>
        </div>
      </div>
    </div>
  );
}
