import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../../lib/api";

export default function MySpace() {
  const [clones, setClones] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancel = false;
    api.get("/clones/mine")
      .then((r) => !cancel && setClones(r.data?.clones || r.data?.items || []))
      .catch(() => !cancel && setClones([]))
      .finally(() => !cancel && setLoading(false));
    return () => { cancel = true; };
  }, []);

  return (
    <section data-testid="my-space-section">
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <div>
          <h2 className="heading-display text-2xl mb-1">My Space</h2>
          <p className="text-sm text-muted">Every clone you've built lives here.</p>
        </div>
        <Link to="/create" className="btn-brutal text-xs" data-testid="my-space-create-btn">
          + New clone
        </Link>
      </div>

      {loading && <div className="text-sm text-muted">Loading your clones…</div>}

      {!loading && clones.length === 0 && (
        <div className="brutal-card p-8 text-center" data-testid="my-space-empty">
          <p className="text-sm text-muted mb-4">You haven't built any clones yet.</p>
          <Link to="/create" className="btn-brutal text-xs inline-block">Build your first clone</Link>
        </div>
      )}

      {!loading && clones.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4" data-testid="my-space-grid">
          {clones.map((c) => (
            <div key={c.clone_id} className="brutal-card p-5 flex flex-col gap-3" data-testid={`my-clone-${c.slug}`}>
              <div className="flex items-start gap-3">
                {c.avatar_url ? (
                  <img src={c.avatar_url} alt="" className="w-12 h-12 rounded-lg object-cover" />
                ) : (
                  <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-amber/30 to-violet/20 flex items-center justify-center text-lg font-display">
                    {(c.display_name || "?")[0].toUpperCase()}
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="font-display text-lg leading-tight truncate" title={c.display_name}>{c.display_name}</div>
                  <div className="text-[10px] font-mono uppercase tracking-widest text-amber/80">/{c.slug}</div>
                </div>
              </div>
              {c.bio && <p className="text-xs text-ink/75 line-clamp-3">{c.bio}</p>}
              <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-muted">
                <span className={`px-2 py-0.5 rounded-full border ${
                  c.visibility === "public" ? "border-emerald-500/40 text-emerald-300" :
                  c.visibility === "private" ? "border-rose/40 text-rose-soft" :
                  "border-white/15 text-ink/70"
                }`}>{c.visibility}</span>
                <span>·</span>
                <span>{c.status}</span>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <Link to={`/${c.slug}`} className="btn-ghost text-[11px] flex-1 text-center" data-testid={`my-clone-${c.slug}-view`}>
                  View
                </Link>
                <Link to={`/clone/${c.clone_id}/edit`} className="btn-brutal text-[11px] flex-1 text-center" data-testid={`my-clone-${c.slug}-edit`}>
                  Edit
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
