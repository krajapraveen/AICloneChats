import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api from "../../lib/api";

function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); }
  catch { return iso; }
}

function statusBadge(status) {
  const s = (status || "").toLowerCase();
  if (s === "ready" || s === "active") return { label: "Active", cls: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10" };
  if (s === "processing" || s === "pending") return { label: "Processing", cls: "border-amber/40 text-amber bg-amber/10" };
  if (s === "failed") return { label: "Failed", cls: "border-rose/40 text-rose-soft bg-rose/10" };
  return { label: status || "—", cls: "border-white/15 text-ink/75 bg-white/[0.03]" };
}

export default function MySpace() {
  const [clones, setClones] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState(null);

  const reload = () => {
    setLoading(true);
    return api.get("/clones/mine")
      .then((r) => {
        const data = r.data;
        const list = Array.isArray(data) ? data : (data?.clones || data?.items || []);
        setClones(list);
      })
      .catch(() => setClones([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, []);

  const onDelete = async (clone) => {
    if (!window.confirm(`Delete clone "${clone.display_name}"? This permanently removes the clone, its chats, memories, and analytics. This cannot be undone.`)) return;
    setDeletingId(clone.clone_id);
    try {
      await api.delete(`/clones/${clone.clone_id}`);
      toast.success("Clone deleted.");
      await reload();
    } catch (err) {
      toast.error(err?.response?.data?.detail?.message || "Could not delete clone.");
    } finally {
      setDeletingId(null);
    }
  };

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
          {clones.map((c) => {
            const status = statusBadge(c.status);
            const category =
              c.personality?.tone ||
              c.demo_category ||
              (c.allowed_topics?.length ? c.allowed_topics[0] : null) ||
              "general";
            return (
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
                <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-muted flex-wrap">
                  <span className={`px-2 py-0.5 rounded-full border ${status.cls}`} data-testid={`my-clone-${c.slug}-status`}>{status.label}</span>
                  <span className={`px-2 py-0.5 rounded-full border ${
                    c.visibility === "public" ? "border-emerald-500/40 text-emerald-300" :
                    c.visibility === "private" ? "border-rose/40 text-rose-soft" :
                    "border-white/15 text-ink/70"
                  }`}>{c.visibility}</span>
                  <span className="px-2 py-0.5 rounded-full border border-violet/40 text-violet-soft" title="Category">{category}</span>
                </div>
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted">
                  Created {formatDate(c.created_at)}
                </div>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  <Link to={`/${c.slug}`} className="btn-ghost text-[11px] flex-1 text-center min-w-[60px]" data-testid={`my-clone-${c.slug}-open`}>
                    Open
                  </Link>
                  <Link to={`/clone/${c.clone_id}/edit`} className="btn-brutal text-[11px] flex-1 text-center min-w-[60px]" data-testid={`my-clone-${c.slug}-edit`}>
                    Edit
                  </Link>
                  <button
                    type="button"
                    onClick={() => onDelete(c)}
                    disabled={deletingId === c.clone_id}
                    className="px-3 py-2 rounded-lg text-[11px] font-medium border border-rose/40 text-rose-soft bg-rose/10 hover:bg-rose/20 disabled:opacity-50 disabled:cursor-not-allowed min-w-[60px]"
                    data-testid={`my-clone-${c.slug}-delete`}
                  >
                    {deletingId === c.clone_id ? "…" : "Delete"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

