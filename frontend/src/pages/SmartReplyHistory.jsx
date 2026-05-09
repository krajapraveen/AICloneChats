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

export default function SmartReplyHistory() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState(null);

  useEffect(() => {
    if (!authLoading && !user) navigate("/login?redirect=/smart-reply/history");
  }, [user, authLoading, navigate]);

  useEffect(() => {
    if (!user) return;
    api.get("/smart-reply/history")
      .then((r) => setSessions(r.data.sessions || []))
      .catch(() => setSessions([]));
  }, [user]);

  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); toast.success("Copied"); } catch { toast.error("Copy failed"); }
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
      <div className="max-w-3xl mx-auto px-5 md:px-8 py-8" data-testid="sr-history-page">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-6">
          <div>
            <span className="tag tag-amber mb-2 inline-block">SMART REPLY · HISTORY</span>
            <h1 className="heading-display text-3xl sm:text-4xl">Your past replies</h1>
          </div>
          <div className="flex gap-3">
            <Link to="/smart-reply" className="btn-brutal text-sm" data-testid="sr-history-back">+ New</Link>
            <Link to="/smart-reply/favorites" className="btn-ghost text-sm" data-testid="sr-history-favorites-link">Favorites</Link>
          </div>
        </div>

        {sessions === null && <div className="text-muted font-mono text-sm">loading…</div>}
        {sessions && sessions.length === 0 && (
          <div className="glass-card p-8 text-center" data-testid="sr-history-empty">
            <p className="font-display text-xl mb-2">Nothing yet.</p>
            <p className="text-sm text-muted mb-5">Generate your first reply and it'll show up here.</p>
            <Link to="/smart-reply" className="btn-brutal text-sm">Start generating →</Link>
          </div>
        )}

        <div className="space-y-4">
          {sessions && sessions.map((s) => (
            <div key={s.session_id} className="brutal-card p-5" data-testid={`sr-history-item-${s.session_id}`}>
              <div className="flex items-center gap-2 flex-wrap mb-3">
                <span className="tag tag-amber">{MODE_LABEL[s.mode] || s.mode?.toUpperCase()}</span>
                <span className="tag">{(s.desired_tone || "").toUpperCase()}</span>
                <span className="text-xs font-mono text-muted ml-auto">
                  {new Date(s.created_at).toLocaleString()}
                </span>
              </div>
              <p className="text-xs font-mono uppercase tracking-widest text-muted mb-1">Incoming</p>
              <p className="text-sm text-ink/85 mb-3 italic line-clamp-2">"{s.incoming_message}"</p>
              <div className="space-y-2">
                {(s.generated_replies || []).map((r, idx) => (
                  <div key={idx} className="bg-white/5 rounded-lg p-3 border border-white/10">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="tag tag-emerald text-[10px]">{(r.label || "").toUpperCase()}</span>
                      <span className="tag text-[10px]">{(r.length || "").toUpperCase()}</span>
                    </div>
                    <p className="text-sm text-ink/90 mb-2 leading-relaxed">{r.reply}</p>
                    <button onClick={() => copy(r.reply)} className="text-xs font-mono uppercase tracking-widest text-amber-soft hover:text-amber" data-testid={`sr-history-copy-${s.session_id}-${idx}`}>
                      Copy →
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
