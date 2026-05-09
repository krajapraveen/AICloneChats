import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const MODE_LABEL = {
  dating: "Dating",
  professional: "Professional",
  apology: "Apology",
  negotiation: "Negotiation",
};

export default function SmartReplyFavorites() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [favs, setFavs] = useState(null);

  useEffect(() => {
    if (!authLoading && !user) navigate("/login?redirect=/smart-reply/favorites");
  }, [user, authLoading, navigate]);

  const load = () => {
    api.get("/smart-reply/favorites")
      .then((r) => setFavs(r.data.favorites || []))
      .catch(() => setFavs([]));
  };

  useEffect(() => { if (user) load(); }, [user]);

  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); toast.success("Copied"); } catch { toast.error("Copy failed"); }
  };

  const remove = async (favId) => {
    try {
      await api.delete(`/smart-reply/favorites/${favId}`);
      setFavs((cur) => (cur || []).filter((f) => f.favorite_id !== favId));
      toast.success("Removed");
    } catch { toast.error("Could not remove"); }
  };

  if (authLoading || !user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8" data-testid="sr-favorites-page">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-6">
          <div>
            <span className="tag tag-violet mb-2 inline-block">SMART REPLY · FAVORITES</span>
            <h1 className="heading-display text-3xl sm:text-4xl">Saved replies</h1>
          </div>
          <div className="flex gap-3">
            <Link to="/smart-reply" className="btn-brutal text-sm" data-testid="sr-fav-new">+ New</Link>
            <Link to="/smart-reply/history" className="btn-ghost text-sm" data-testid="sr-fav-history-link">History</Link>
          </div>
        </div>

        {favs === null && <div className="text-muted font-mono text-sm">loading…</div>}
        {favs && favs.length === 0 && (
          <div className="glass-card p-8 text-center" data-testid="sr-favorites-empty">
            <p className="font-display text-xl mb-2">No favorites yet.</p>
            <p className="text-sm text-muted mb-5">Tap ☆ Save on any generated reply to keep it here.</p>
            <Link to="/smart-reply" className="btn-brutal text-sm">Generate a reply →</Link>
          </div>
        )}

        <div className="space-y-3">
          {favs && favs.map((f) => (
            <div key={f.favorite_id} className="brutal-card p-5" data-testid={`sr-fav-item-${f.favorite_id}`}>
              <div className="flex items-center gap-2 flex-wrap mb-2">
                <span className="tag tag-amber">{MODE_LABEL[f.mode] || f.mode?.toUpperCase()}</span>
                <span className="tag tag-emerald">{(f.label || "").toUpperCase()}</span>
                <span className="text-xs font-mono text-muted ml-auto">
                  {new Date(f.created_at).toLocaleDateString()}
                </span>
              </div>
              <p className="text-base text-ink leading-relaxed mb-3 whitespace-pre-wrap">{f.reply_text}</p>
              <div className="flex gap-2">
                <button onClick={() => copy(f.reply_text)} className="btn-brutal text-sm" data-testid={`sr-fav-copy-${f.favorite_id}`}>Copy</button>
                <button onClick={() => remove(f.favorite_id)} className="btn-ghost text-sm" data-testid={`sr-fav-remove-${f.favorite_id}`}>Remove</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
