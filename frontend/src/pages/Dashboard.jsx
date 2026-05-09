import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

export default function Dashboard() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [clones, setClones] = useState([]);
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

  if (authLoading || !user) {
    return <div className="min-h-screen bg-cream flex items-center justify-center font-display">Loading…</div>;
  }

  return (
    <div className="min-h-screen bg-cream">
      <Navbar />
      <div className="max-w-6xl mx-auto px-5 md:px-8 py-10 md:py-14" data-testid="dashboard-page">
        <div className="flex items-end justify-between gap-4 flex-wrap mb-10">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-muted mb-2">CLONE HQ</p>
            <h1 className="heading-display text-4xl md:text-5xl">Hey {user.name?.split(" ")[0] || "you"}.</h1>
            <p className="mt-2 font-medium text-muted">Your AI selves, all in one place.</p>
          </div>
          <Link to="/clones/new" className="btn-brutal" data-testid="dashboard-create-clone-btn">
            + New clone
          </Link>
        </div>

        {loading ? (
          <p className="font-display text-ink">Loading your clones…</p>
        ) : clones.length === 0 ? (
          <div className="glass-card p-10 text-center" data-testid="empty-state">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-amber to-violet mb-5 shadow-glow-amber">
              <span className="text-3xl">✨</span>
            </div>
            <h2 className="heading-display text-3xl mb-2">No clones yet.</h2>
            <p className="text-muted font-medium mb-6">Build your first AI version. Takes 3 minutes.</p>
            <Link to="/clones/new" className="btn-brutal" data-testid="empty-create-btn">Create your clone</Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" data-testid="clones-grid">
            {clones.map((c) => (
              <div key={c.clone_id} className="brutal-card p-6 flex flex-col" data-testid={`clone-card-${c.slug}`}>
                <div className="flex items-start gap-3 mb-4">
                  {c.avatar_url ? (
                    <img src={c.avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${c.avatar_url}` : c.avatar_url} alt={c.display_name} className="w-14 h-14 rounded-full border border-white/15 object-cover" />
                  ) : (
                    <div className="w-14 h-14 rounded-full bg-gradient-to-br from-violet to-amber flex items-center justify-center font-display font-black text-bg text-xl">
                      {c.display_name?.[0]?.toUpperCase() || "C"}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <h3 className="font-display font-bold text-xl truncate text-ink">{c.display_name}</h3>
                    <p className="font-mono text-xs text-muted truncate">/{c.slug}</p>
                  </div>
                  <span className={`tag ${c.visibility === "public" ? "tag-emerald" : c.visibility === "private" ? "tag-rose" : "tag-violet"}`}>
                    {c.visibility}
                  </span>
                </div>

                {c.bio && <p className="text-sm font-medium text-ink/70 mb-4 line-clamp-2">{c.bio}</p>}

                <div className="mt-auto grid grid-cols-2 gap-2">
                  <Link to={`/clones/${c.clone_id}/edit`} className="btn-ghost text-xs py-2" data-testid={`edit-clone-${c.slug}`}>Edit</Link>
                  <Link to={`/clones/${c.clone_id}/memories`} className="btn-ghost text-xs py-2" data-testid={`memories-clone-${c.slug}`}>Memories</Link>
                  <Link to={`/${c.slug}`} className="btn-ghost text-xs py-2 col-span-1" data-testid={`view-clone-${c.slug}`}>Public page</Link>
                  <button onClick={() => copyShareLink(c.slug)} className="btn-brutal text-xs py-2" data-testid={`share-clone-${c.slug}`}>Copy link</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
