/**
 * Conversation Memory — read-only archive of past artifact extractions
 * across all conversations the authenticated user has had.
 *
 * Thesis surface: "the system remembers what mattered."
 * NOT a productivity dashboard. NO scheduling. NO reminders. NO nudges.
 * Pull-only — clicking an artifact takes you back to the clone that produced it.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function fmtAgo(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (sec < 60) return "just now";
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  } catch { return iso; }
}

function ArtifactCard({ a }) {
  const taskCount = (a.tasks || []).length;
  const decisionCount = (a.decisions || []).length;
  const followUpCount = (a.follow_ups || []).length;
  const link = a.clone_slug ? `/${a.clone_slug}` : null;
  const Wrap = link ? Link : "div";
  const wrapProps = link ? { to: link, "data-testid": `memory-artifact-${a.artifact_id}` } : { "data-testid": `memory-artifact-${a.artifact_id}` };
  return (
    <Wrap {...wrapProps} className="brutal-card p-5 block hover:border-amber/40 transition">
      <div className="flex items-start gap-3 mb-2">
        {a.clone_avatar_url ? (
          <img src={a.clone_avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${a.clone_avatar_url}` : a.clone_avatar_url} alt="" className="w-10 h-10 rounded-full object-cover border border-white/10" />
        ) : (
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-sm">
            {a.clone_display_name?.[0]?.toUpperCase() || "?"}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="text-sm font-bold truncate">{a.clone_display_name || "Untitled clone"}</div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{fmtAgo(a.created_at)} · from {a.message_count_at_extraction || 0} msgs</div>
        </div>
      </div>
      {a.summary && (
        <div className="text-sm text-ink/85 whitespace-pre-wrap line-clamp-3 mb-3" data-testid={`memory-summary-${a.artifact_id}`}>{a.summary}</div>
      )}
      <div className="flex flex-wrap gap-2 text-[10px] font-mono uppercase tracking-widest">
        {taskCount > 0 && <span className="px-2 py-0.5 rounded-full border border-amber/40 text-amber">{taskCount} task{taskCount === 1 ? "" : "s"}</span>}
        {decisionCount > 0 && <span className="px-2 py-0.5 rounded-full border border-violet-400/40 text-violet-300">{decisionCount} decision{decisionCount === 1 ? "" : "s"}</span>}
        {followUpCount > 0 && <span className="px-2 py-0.5 rounded-full border border-emerald/40 text-emerald-soft">{followUpCount} follow-up{followUpCount === 1 ? "" : "s"}</span>}
        {(a.unresolved_questions || []).length > 0 && <span className="px-2 py-0.5 rounded-full border border-ink/30 text-muted">{a.unresolved_questions.length} open question{a.unresolved_questions.length === 1 ? "" : "s"}</span>}
      </div>
    </Wrap>
  );
}

export default function ConversationMemory() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [artifacts, setArtifacts] = useState([]);
  const [fetching, setFetching] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setFetching(true);
      const r = await api.get("/clone-artifacts");
      setArtifacts(r.data?.artifacts || []);
    } catch {
      setArtifacts([]);
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    if (!loading && !user) { navigate("/login?redirect=/conversation-memory"); return; }
    if (user) refresh();
  }, [user, loading, navigate, refresh]);

  if (loading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="conversation-memory-page">
      <Navbar />
      <div className="max-w-4xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10">
        <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Memory · pull-only</div>
        <h1 className="heading-display text-2xl sm:text-3xl mt-1">Conversation memory</h1>
        <p className="text-xs text-muted mt-1 max-w-xl mb-6">Past extractions from your clone conversations, newest first. Tap any one to return to the clone that produced it. The system remembers; it does not chase.</p>

        {fetching && <div className="text-muted text-sm font-mono">loading…</div>}

        {!fetching && artifacts.length === 0 && (
          <div className="brutal-card p-8 text-center" data-testid="memory-empty">
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">No memory yet</div>
            <div className="text-sm text-ink/80 mb-4">When you chat with a clone and ask it to extract what mattered, the result lives here.</div>
            <Link to="/explore" className="btn-brutal text-sm" data-testid="memory-empty-explore">Find a clone →</Link>
          </div>
        )}

        {!fetching && artifacts.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="memory-list">
            {artifacts.map((a) => <ArtifactCard key={a.artifact_id} a={a} />)}
          </div>
        )}
      </div>
    </div>
  );
}
