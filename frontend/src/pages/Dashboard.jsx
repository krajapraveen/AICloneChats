import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { formatCount } from "../lib/format";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

function StatTile({ label, value, tone, testId }) {
  return (
    <div className={`brutal-card p-4 sm:p-5 ${tone || ""}`} data-testid={testId}>
      <p className="label-brutal mb-1.5">{label}</p>
      <p className="heading-display text-2xl sm:text-3xl">{value ?? "—"}</p>
    </div>
  );
}

function WorkspaceCard({ tone, kicker, title, body, primary, secondary, icon, testId }) {
  return (
    <div className="brutal-card p-6 flex flex-col h-full group hover:translate-y-[-2px] transition-transform" data-testid={testId}>
      <div className="flex items-center justify-between gap-3 mb-3">
        <span className={`tag ${tone}`}>{kicker}</span>
        <div className="w-9 h-9 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-base">
          {icon}
        </div>
      </div>
      <h3 className="heading-display text-2xl mb-2">{title}</h3>
      <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5 flex-1">{body}</p>
      <div className="flex flex-wrap gap-2">
        <Link to={primary.to} className="btn-brutal text-sm" data-testid={`${testId}-primary`}>{primary.label}</Link>
        {secondary && (
          <Link to={secondary.to} className="btn-ghost text-sm" data-testid={`${testId}-secondary`}>{secondary.label}</Link>
        )}
      </div>
    </div>
  );
}

function ComingSoonCard({ tone, kicker, title, body, icon, testId, onInterest, accent = "rose" }) {
  const accentText = accent === "sky" ? "text-sky-300/80" : "text-rose-300/80";
  const accentBg = accent === "sky" ? "bg-sky-500/10" : "bg-rose-500/10";
  const accentBorder = accent === "sky" ? "border-sky-400/30" : "border-rose-400/30";
  return (
    <div className="brutal-card p-6 flex flex-col h-full relative overflow-hidden" data-testid={testId}>
      <div className={`absolute top-3 right-3 text-[10px] font-mono uppercase tracking-widest ${accentText} ${accentBg} border ${accentBorder} rounded-full px-2 py-0.5`}>
        Coming soon
      </div>
      <div className="flex items-center justify-between gap-3 mb-3">
        <span className={`tag ${tone}`}>{kicker}</span>
        <div className="w-9 h-9 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-base">
          {icon}
        </div>
      </div>
      <h3 className="heading-display text-2xl mb-2">{title}</h3>
      <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5 flex-1">{body}</p>
      <button
        type="button"
        onClick={onInterest}
        className="btn-ghost text-sm self-start"
        data-testid={`${testId}-interest`}
      >
        Notify me when it's live
      </button>
    </div>
  );
}

export default function Dashboard() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [clones, setClones] = useState([]);
  const [stats, setStats] = useState({}); // clone_id -> stats
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!authLoading && !user) navigate("/login");
  }, [authLoading, user, navigate]);

  useEffect(() => {
    if (!user) return;
    (async () => {
      try {
        const { data } = await api.get("/clones/mine");
        setClones(data);
        const results = await Promise.all(
          (data || []).map((c) =>
            api.get(`/analytics/stats/${c.slug}`).then((r) => [c.clone_id, r.data]).catch(() => [c.clone_id, null])
          )
        );
        const map = {};
        results.forEach(([id, s]) => { if (s) map[id] = s; });
        setStats(map);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, [user]);

  const copyShareLink = (slug) => {
    const url = `${window.location.origin}/${slug}`;
    navigator.clipboard.writeText(url);
    toast.success("Share link copied!");
  };

  // Aggregate stats across all clones for the insights strip
  const totals = useMemo(() => {
    const cloneIds = Object.keys(stats);
    let messages = 0, visitors = 0, shares = 0;
    cloneIds.forEach((id) => {
      messages += stats[id]?.message_count || 0;
      visitors += stats[id]?.visitor_count || 0;
      shares += stats[id]?.share_count || 0;
    });
    const publicCount = (clones || []).filter((c) => c.visibility === "public").length;
    return {
      total: clones.length,
      publicCount,
      messages,
      visitors,
      shares,
    };
  }, [clones, stats]);

  if (authLoading || !user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  const firstName = user.name?.split(" ")[0] || "you";
  const hasClones = clones.length > 0;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="orb orb-amber w-[420px] h-[420px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[380px] h-[380px] top-72 -left-32 opacity-20 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10 relative" data-testid="dashboard-page">
        {/* HERO */}
        <section className="grid grid-cols-1 lg:grid-cols-12 gap-5 mb-10" data-testid="dashboard-hero">
          <div className="lg:col-span-7 glass-card p-6 sm:p-8 flex flex-col justify-between relative overflow-hidden">
            <div className="absolute -top-12 -right-12 w-48 h-48 rounded-full bg-amber/15 blur-3xl pointer-events-none" />
            <div className="relative">
              <p className="label-brutal mb-2">CLONE HQ · WORKSPACE</p>
              <h1 className="heading-display text-3xl sm:text-4xl md:text-5xl mb-3">Welcome back, {firstName}.</h1>
              <p className="text-sm sm:text-base font-medium text-ink/75 max-w-lg leading-relaxed">
                Create AI personalities, explore adaptive conversations, and craft smart replies — all in one workspace.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 mt-6 relative">
              <Link to="/clones/new" className="btn-brutal text-sm" data-testid="hero-action-create-clone">+ Create AI Clone</Link>
              <Link to="/mood-chat" className="btn-violet text-sm" data-testid="hero-action-mood-chat">Mood-Based Chat</Link>
              <Link to="/smart-reply" className="btn-ghost text-sm" data-testid="hero-action-smart-reply">Smart Reply</Link>
              <Link to="/voice" className="btn-ghost text-sm" data-testid="hero-action-voice">Voice → Message</Link>
            </div>
          </div>

          {/* Stat tiles */}
          <div className="lg:col-span-5 grid grid-cols-2 gap-3" data-testid="dashboard-stats-grid">
            <StatTile label="My clones" value={totals.total} testId="stat-clones-total" />
            <StatTile label="Public" value={totals.publicCount} testId="stat-clones-public" />
            <StatTile label="Conversations" value={formatCount(totals.messages)} testId="stat-messages" />
            <StatTile label="Visitors" value={formatCount(totals.visitors)} testId="stat-visitors" />
          </div>
        </section>

        {/* WORKSPACE OVERVIEW */}
        <section className="mb-10" data-testid="workspace-overview">
          <div className="flex items-end justify-between gap-3 mb-4">
            <div>
              <p className="label-brutal mb-1">Tools in your workspace</p>
              <h2 className="heading-display text-2xl sm:text-3xl">Nine ways to talk to AI.</h2>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <WorkspaceCard
              testId="workspace-card-clone"
              tone="tag-amber"
              kicker="PERSONALITY-FIRST"
              icon="◉"
              title="AI Clone Chat"
              body="Build and chat with AI personalities that respond with custom tone, memory, and behavior. Your clone, your rules."
              primary={{ to: hasClones ? "/explore" : "/clones/new", label: hasClones ? "Explore clones" : "Create clone" }}
              secondary={{ to: "/clones/new", label: "New clone" }}
            />
            <WorkspaceCard
              testId="workspace-card-mood"
              tone="tag-violet"
              kicker="EMOTION-FIRST"
              icon="◐"
              title="Mood-Based Chat"
              body="Emotionally adaptive AI that adjusts tone based on how you're feeling. No setup. Just type."
              primary={{ to: "/mood-chat", label: "Start mood chat" }}
            />
            <WorkspaceCard
              testId="workspace-card-smart-reply"
              tone="tag-emerald"
              kicker="UTILITY-FIRST"
              icon="✎"
              title="Smart Reply"
              body="Generate copy-ready replies for dating, professional, apology, and negotiation messages. Three tones, one tap to copy."
              primary={{ to: "/smart-reply", label: "Open Smart Reply" }}
              secondary={{ to: "/smart-reply/favorites", label: "Favorites" }}
            />
            <WorkspaceCard
              testId="workspace-card-voice"
              tone="tag-emerald"
              kicker="VOICE-FIRST"
              icon="🎙"
              title="Voice → Message"
              body="Speak it, upload a voice note, or paste rough text. We write 6 polished tone-matched messages instantly. Audio is never stored."
              primary={{ to: "/voice", label: "Open Voice studio" }}
              secondary={{ to: "/voice/history", label: "History" }}
            />
            <WorkspaceCard
              testId="workspace-card-anonymous"
              tone="tag-rose"
              kicker="ANONYMOUS-FIRST"
              icon="◌"
              title="Anonymous Reality"
              body="Topic rooms where strangers talk honestly. No names, no fake flexing. AI moderation keeps every room emotionally safe."
              primary={{ to: "/anonymous-reality", label: "Enter anonymously" }}
            />
            <WorkspaceCard
              testId="workspace-card-delayed"
              tone="tag-rose"
              kicker="PERSISTENCE-FIRST"
              icon="⌛"
              title="Delayed-Delivery Emotional Chat"
              body="Write something now, sealed until a future date. Delivered to your future self, someone you care about, or by email. The system delivers; it does not chase."
              primary={{ to: "/delayed-chat", label: "Open delayed chat" }}
            />
            <WorkspaceCard
              testId="workspace-card-debate"
              tone="tag-sky"
              kicker="DEBATE-FIRST"
              icon="⚖"
              title="AI Debate Rooms"
              body="Live debate rooms with AI scoring, crowd voting, and real-time ranking. Pick a side, argue your case, win a shareable badge."
              primary={{ to: "/debates", label: "Enter debate room" }}
            />
            <WorkspaceCard
              testId="workspace-card-translation"
              tone="tag-amber"
              kicker="TRANSLATION-FIRST"
              icon="ことば"
              title="Translation Chat"
              body="Type in your language. Everyone reads in theirs. AI translates each message in real time across English, Hindi, Telugu, and Japanese."
              primary={{ to: "/translation-chat", label: "Open translation chat" }}
            />
            <WorkspaceCard
              testId="workspace-card-memory"
              tone="tag-violet"
              kicker="MEMORY-FIRST"
              icon="◈"
              title="Conversation Memory"
              body="The clone remembers what mattered in your conversations — tasks, decisions, follow-ups, summaries. Pull-only. No reminders, no nudges, no chasing."
              primary={{ to: "/conversation-memory", label: "Open memory" }}
            />
          </div>
        </section>

        {/* Admin utilities have moved to a dedicated /admin index. Keeping the
            dashboard purely a product surface — even for admin accounts. */}

        {/* MY CLONES */}
        <section data-testid="my-clones-section">
          <div className="flex items-end justify-between gap-3 mb-4 flex-wrap">
            <div>
              <p className="label-brutal mb-1">Your library</p>
              <h2 className="heading-display text-2xl sm:text-3xl">My AI Clones</h2>
              <p className="text-sm font-medium text-muted mt-1">Your personalized AI personalities and public experiences.</p>
            </div>
            <Link to="/clones/new" className="btn-brutal text-sm" data-testid="my-clones-new-btn">+ New clone</Link>
          </div>

          {loading ? (
            <div className="glass-card p-10 text-center text-muted font-mono text-sm">Loading your clones…</div>
          ) : !hasClones ? (
            <div className="glass-card p-8 sm:p-10 text-center relative overflow-hidden" data-testid="empty-state">
              <div className="absolute inset-0 pointer-events-none opacity-50">
                <div className="absolute top-0 left-1/2 -translate-x-1/2 w-72 h-72 rounded-full bg-violet/10 blur-3xl" />
              </div>
              <div className="relative">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-amber to-violet mb-5 shadow-glow-amber">
                  <span className="text-2xl">✨</span>
                </div>
                <h3 className="heading-display text-2xl sm:text-3xl mb-2">Your AI workspace is ready.</h3>
                <p className="text-sm text-muted font-medium mb-6 max-w-md mx-auto leading-relaxed">
                  Create your first AI personality, explore adaptive mood conversations, or generate smart replies for real-world chats.
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  <Link to="/clones/new" className="btn-brutal text-sm" data-testid="empty-create-btn">Create First Clone</Link>
                  <Link to="/mood-chat" className="btn-violet text-sm" data-testid="empty-mood-btn">Try Mood Chat</Link>
                  <Link to="/smart-reply" className="btn-ghost text-sm" data-testid="empty-smart-reply-btn">Try Smart Reply</Link>
                  <Link to="/voice" className="btn-ghost text-sm" data-testid="empty-voice-btn">Try Voice</Link>
                </div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" data-testid="clones-grid">
              {clones.map((c) => {
                const s = stats[c.clone_id];
                return (
                  <div key={c.clone_id} className="brutal-card p-6 flex flex-col group hover:translate-y-[-2px] transition-transform" data-testid={`clone-card-${c.slug}`}>
                    <div className="flex items-start gap-3 mb-4">
                      {c.avatar_url ? (
                        <img src={c.avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${c.avatar_url}` : c.avatar_url} alt={c.display_name} className="w-14 h-14 rounded-full border border-white/15 object-cover ring-2 ring-amber/20 group-hover:ring-amber/50 transition" />
                      ) : (
                        <div className="w-14 h-14 rounded-full bg-gradient-to-br from-violet to-amber flex items-center justify-center font-display font-black text-bg text-xl ring-2 ring-amber/20 group-hover:ring-amber/50 transition">
                          {c.display_name?.[0]?.toUpperCase() || "C"}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <h3 className="font-display font-bold text-xl truncate text-ink">{c.display_name}</h3>
                        <p className="font-mono text-xs text-muted truncate">aiclonechats.com/{c.slug}</p>
                      </div>
                      <span className={`tag ${c.visibility === "public" ? "tag-emerald" : c.visibility === "private" ? "tag-rose" : "tag-violet"}`}>
                        {c.visibility}
                      </span>
                    </div>

                    {c.bio && <p className="text-sm font-medium text-ink/70 mb-3 line-clamp-2 leading-relaxed">{c.bio}</p>}

                    {s && (s.share_count > 0 || s.message_count > 0 || s.visitor_count > 0) && (
                      <div className="flex items-center gap-3 mb-4 text-[11px] font-mono uppercase tracking-wider text-muted" data-testid={`stats-${c.slug}`}>
                        <span title={`${s.share_count} shares`}>✨ {formatCount(s.share_count)}</span>
                        <span title={`${s.message_count} chats`}>💬 {formatCount(s.message_count)}</span>
                        <span title={`${s.visitor_count} visitors`}>● {formatCount(s.visitor_count)}</span>
                      </div>
                    )}

                    <div className="mt-auto grid grid-cols-2 gap-2">
                      <Link to={`/clones/${c.clone_id}/edit`} className="btn-ghost text-xs py-2" data-testid={`edit-clone-${c.slug}`}>Edit</Link>
                      <Link to={`/clones/${c.clone_id}/memories`} className="btn-ghost text-xs py-2" data-testid={`memories-clone-${c.slug}`}>Memories</Link>
                      <Link to={`/${c.slug}`} className="btn-ghost text-xs py-2" data-testid={`view-clone-${c.slug}`}>Public page</Link>
                      <button onClick={() => copyShareLink(c.slug)} className="btn-brutal text-xs py-2" data-testid={`share-clone-${c.slug}`}>Copy link</button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
