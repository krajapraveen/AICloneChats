/**
 * Debate Room — main interaction surface.
 *
 * Sections:
 *   1. Header: title, sides at-a-glance, leading side pill, leaderboard link
 *   2. Side picker (if user hasn't joined)
 *   3. Argument composer (if joined)
 *   4. Two-column argument feed (Side A / Side B), each ranked by rank_score
 *
 * Mobile: sides stack vertically. Composer is sticky.
 */
import { memo, useCallback, useMemo, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import useDebateRoom from "../hooks/useDebateRoom";

function relativeTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m`;
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}h`;
  return `${Math.floor(diff / 86400_000)}d`;
}

function ScoreBadge({ score, breakdown }) {
  const tone = score >= 75 ? "text-emerald-soft border-emerald/40 bg-emerald-500/10"
    : score >= 50 ? "text-amber-soft border-amber/40 bg-amber-500/10"
    : "text-rose-300 border-rose/40 bg-rose-500/10";
  return (
    <div className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full border text-[10px] font-mono uppercase tracking-widest ${tone}`} title={breakdown ? `clarity ${breakdown.clarity} · logic ${breakdown.logic} · evidence ${breakdown.evidence} · originality ${breakdown.originality} · civility ${breakdown.civility} · persuasiveness ${breakdown.persuasiveness}` : ""} data-testid="argument-ai-score">
      <span className="font-display font-black text-base leading-none">{score}</span>
      <span>AI</span>
    </div>
  );
}

function VoteButtons({ arg, onVote, disabled }) {
  return (
    <div className="flex items-center gap-1" data-testid={`argument-vote-${arg.argument_id}`}>
      <button
        type="button"
        onClick={() => onVote(arg.argument_id, arg.my_vote === "up" ? "clear" : "up")}
        disabled={disabled}
        className={`flex items-center gap-1 px-2 py-1 rounded-full border text-xs font-mono transition-colors ${arg.my_vote === "up" ? "border-emerald text-emerald-soft bg-emerald-500/15" : "border-ink/15 text-ink/70 hover:border-emerald/40 hover:text-emerald-soft"} disabled:opacity-40`}
        data-testid={`argument-vote-up-${arg.argument_id}`}
        aria-label="upvote"
      >
        ▲ {arg.upvotes || 0}
      </button>
      <button
        type="button"
        onClick={() => onVote(arg.argument_id, arg.my_vote === "down" ? "clear" : "down")}
        disabled={disabled}
        className={`flex items-center gap-1 px-2 py-1 rounded-full border text-xs font-mono transition-colors ${arg.my_vote === "down" ? "border-rose-400 text-rose-300 bg-rose-500/15" : "border-ink/15 text-ink/70 hover:border-rose-400/40 hover:text-rose-300"} disabled:opacity-40`}
        data-testid={`argument-vote-down-${arg.argument_id}`}
        aria-label="downvote"
      >
        ▼ {arg.downvotes || 0}
      </button>
    </div>
  );
}

function ArgumentCardImpl({ a, onVote, onReport, voteDisabled }) {
  const isHidden = a.moderation_status === "hidden";
  return (
    <div className="brutal-card p-4 sm:p-5" data-testid={`argument-card-${a.argument_id}`}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[10px] font-mono uppercase tracking-widest text-ink/60 truncate" data-testid={`argument-handle-${a.argument_id}`}>{a.anonymous_handle}</span>
          {a.is_mine && <span className="tag tag-amber text-[9px]">YOU</span>}
          <span className="text-[10px] font-mono text-muted">{relativeTime(a.created_at)}</span>
        </div>
        <ScoreBadge score={a.ai_score} breakdown={a.ai_score_breakdown} />
      </div>
      <p className={`text-sm leading-relaxed whitespace-pre-wrap break-words ${isHidden ? "italic text-muted" : "text-ink/90"}`} data-testid={`argument-content-${a.argument_id}`}>
        {a.content}
      </p>
      {a.ai_feedback && !isHidden && (
        <div className="text-[11px] font-mono text-muted mt-3 leading-relaxed border-l-2 border-ink/15 pl-3 italic" data-testid={`argument-feedback-${a.argument_id}`}>
          {a.ai_feedback}
        </div>
      )}
      <div className="flex items-center justify-between mt-3">
        <VoteButtons arg={a} onVote={onVote} disabled={voteDisabled || a.is_mine} />
        {!a.is_mine && !isHidden && (
          <button onClick={() => onReport(a)} className="text-[10px] font-mono uppercase tracking-widest text-muted hover:text-rose-300" data-testid={`argument-report-${a.argument_id}`}>
            ⚐ Report
          </button>
        )}
      </div>
    </div>
  );
}

const ArgumentCard = memo(ArgumentCardImpl, (prev, next) => {
  if (prev.voteDisabled !== next.voteDisabled) return false;
  if (prev.onVote !== next.onVote || prev.onReport !== next.onReport) return false;
  const a = prev.a, b = next.a;
  return (
    a.argument_id === b.argument_id &&
    a.content === b.content &&
    a.ai_score === b.ai_score &&
    a.ai_feedback === b.ai_feedback &&
    a.upvotes === b.upvotes &&
    a.downvotes === b.downvotes &&
    a.moderation_status === b.moderation_status &&
    a.my_vote === b.my_vote &&
    a.is_mine === b.is_mine
  );
});

function SidePicker({ debate, onJoin, busy }) {
  return (
    <div className="brutal-card p-5 sm:p-6 mb-6" data-testid="debate-side-picker">
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Pick your side</div>
      <h3 className="font-display font-bold text-xl mb-4">Which side will you argue?</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <button
          onClick={() => onJoin("A")}
          disabled={busy}
          data-testid="debate-join-a"
          className="brutal-card p-5 text-left hover:border-emerald hover:translate-y-[-2px] transition-all"
        >
          <div className="text-[10px] font-mono uppercase tracking-widest text-emerald-soft mb-1">SIDE A</div>
          <div className="font-display font-bold text-lg text-ink">{debate.side_a_label}</div>
        </button>
        <button
          onClick={() => onJoin("B")}
          disabled={busy}
          data-testid="debate-join-b"
          className="brutal-card p-5 text-left hover:border-rose-400 hover:translate-y-[-2px] transition-all"
        >
          <div className="text-[10px] font-mono uppercase tracking-widest text-rose-300 mb-1">SIDE B</div>
          <div className="font-display font-bold text-lg text-ink">{debate.side_b_label}</div>
        </button>
      </div>
      <div className="text-[10px] font-mono text-muted mt-3">You can't switch sides after submitting.</div>
    </div>
  );
}

function Composer({ debate, mySide, onSubmit, busy }) {
  const [draft, setDraft] = useState("");
  const sideLabel = mySide === "A" ? debate.side_a_label : debate.side_b_label;
  const len = draft.length;
  const tooShort = len < 10;
  const tooLong = len > 4000;

  return (
    <div className="brutal-card p-4 sm:p-5 mb-6" data-testid="debate-composer">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-mono uppercase tracking-widest text-muted">
          Make your case · arguing for <span className="text-amber" data-testid="debate-composer-side">{sideLabel}</span>
        </div>
        <span className={`text-[10px] font-mono ${tooLong ? "text-rose-300" : "text-muted"}`}>{len}/4000</span>
      </div>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Lay it out. Logic, evidence, civility — AI scores all of it."
        className="input-brutal w-full min-h-[120px] resize-y"
        maxLength={4200}
        disabled={busy}
        data-testid="debate-composer-input"
      />
      <div className="flex items-center justify-between gap-2 mt-3">
        <div className="text-[10px] font-mono text-muted hidden sm:block">
          AI scores: clarity · logic · evidence · originality · civility · persuasiveness
        </div>
        <button
          type="button"
          onClick={async () => {
            const txt = draft.trim();
            if (txt.length < 10 || txt.length > 4000 || busy) return;
            const ok = await onSubmit(txt);
            if (ok) setDraft("");
          }}
          disabled={busy || tooShort || tooLong}
          className="btn-brutal text-sm disabled:opacity-50"
          data-testid="debate-composer-submit"
        >
          {busy ? "Scoring…" : "Submit argument"}
        </button>
      </div>
    </div>
  );
}

function SidePanel({ side, label, args, total, leading, onVote, onReport, voteDisabled }) {
  const tone = side === "A" ? "emerald" : "rose";
  return (
    <div data-testid={`debate-side-panel-${side}`}>
      <div className="flex items-baseline justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-mono uppercase tracking-widest text-${tone}-300`}>SIDE {side}</span>
          <span className="font-display font-bold text-base text-ink">{label}</span>
          {leading && <span className="tag tag-amber text-[9px]">LEADING</span>}
        </div>
        <span className="text-[10px] font-mono text-muted">{total} args</span>
      </div>
      <div className="space-y-3">
        {args.length === 0 && (
          <div className="brutal-card p-6 text-center text-xs font-mono text-muted">No arguments yet.</div>
        )}
        {args.map((a) => (
          <ArgumentCard key={a.argument_id} a={a} onVote={onVote} onReport={onReport} voteDisabled={voteDisabled} />
        ))}
      </div>
    </div>
  );
}

export default function DebateRoom() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { debate, args, leaderboard, status, error, refresh } = useDebateRoom(slug);
  const [busy, setBusy] = useState(false);

  const mySide = debate?.my_side || null;
  const sideA = useMemo(() => args.filter((x) => x.side === "A"), [args]);
  const sideB = useMemo(() => args.filter((x) => x.side === "B"), [args]);
  const leadingSide = leaderboard?.leading_side || null;

  const requireAuth = useCallback((next) => {
    if (!user) {
      navigate(`/login?redirect=/debates/${slug}`);
      return false;
    }
    return true;
  }, [user, navigate, slug]);

  const onJoin = useCallback(async (side) => {
    if (!requireAuth()) return;
    setBusy(true);
    try {
      await api.post(`/debates/${slug}/join`, { side });
      api.post(`/debates/${slug}/track`, { event_name: "debate_joined", metadata: { side } }).catch(() => {});
      toast.success(`You're on side ${side}.`);
      refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not join");
    } finally {
      setBusy(false);
    }
  }, [slug, requireAuth, refresh]);

  const onSubmit = useCallback(async (content) => {
    if (!requireAuth()) return false;
    setBusy(true);
    try {
      const r = await api.post(`/debates/${slug}/arguments`, { side: mySide, content });
      const a = r.data?.argument;
      if (a?.moderation_status === "hidden") {
        toast.error("AI flagged this — try a more civil version.");
      } else {
        toast.success(`Scored ${a?.ai_score || 0}/100`);
      }
      refresh();
      return true;
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not submit");
      return false;
    } finally {
      setBusy(false);
    }
  }, [slug, mySide, requireAuth, refresh]);

  const onVote = useCallback(async (argument_id, vote_type) => {
    if (!requireAuth()) return;
    try {
      await api.post(`/debates/arguments/${argument_id}/vote`, { vote_type });
      refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Vote failed");
    }
  }, [requireAuth, refresh]);

  const onReport = useCallback(async (a) => {
    if (!requireAuth()) return;
    const reason = window.prompt("Why are you reporting this argument?");
    if (!reason || !reason.trim()) return;
    try {
      await api.post(`/debates/arguments/${a.argument_id}/report`, { reason: reason.trim() });
      toast.success("Reported. Admins will review.");
      refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Report failed");
    }
  }, [requireAuth, refresh]);

  if (status === "loading" && !debate) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm" data-testid="debate-room-loading">loading debate…</div>
      </div>
    );
  }

  if (error && !debate) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 py-10">
          <div className="brutal-card p-8 text-center" data-testid="debate-room-error">
            <h1 className="heading-display text-2xl mb-2">{error}</h1>
            <Link to="/debates" className="btn-ghost mt-4 inline-block">← All debates</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="debate-room-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8">
        <Link to="/debates" className="text-xs font-mono text-muted hover:text-ink mb-3 inline-block" data-testid="debate-room-back">← All debates</Link>

        <header className="mb-6">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="tag tag-sky text-[10px]">{debate?.category}</span>
            {debate?.is_featured && <span className="tag tag-amber text-[10px]">FEATURED</span>}
            <span className="tag text-[10px]" data-testid="debate-status-pill">{debate?.status?.toUpperCase()}</span>
            {leadingSide && (
              <span className="tag tag-amber text-[10px]" data-testid="debate-leading-pill">
                LEADING: {leadingSide === "A" ? debate.side_a_label : debate.side_b_label}
              </span>
            )}
          </div>
          <h1 className="heading-display text-2xl sm:text-4xl leading-tight" data-testid="debate-room-title">{debate?.title}</h1>
          <p className="text-sm sm:text-base text-muted mt-3 leading-relaxed">{debate?.description}</p>
        </header>

        {!mySide && debate?.status === "active" && (
          <SidePicker debate={debate} onJoin={onJoin} busy={busy} />
        )}
        {mySide && debate?.status === "active" && (
          <Composer debate={debate} mySide={mySide} onSubmit={onSubmit} busy={busy} />
        )}
        {debate?.status !== "active" && (
          <div className="brutal-card p-5 mb-6 text-sm text-amber-soft" data-testid="debate-ended-banner">
            This debate has ended.{" "}
            <Link className="underline" to={`/debates/${slug}/results`}>See results →</Link>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 sm:gap-6" data-testid="debate-feeds">
          <SidePanel
            side="A"
            label={debate?.side_a_label}
            args={sideA}
            total={sideA.length}
            leading={leadingSide === "A"}
            onVote={onVote}
            onReport={onReport}
            voteDisabled={busy}
          />
          <SidePanel
            side="B"
            label={debate?.side_b_label}
            args={sideB}
            total={sideB.length}
            leading={leadingSide === "B"}
            onVote={onVote}
            onReport={onReport}
            voteDisabled={busy}
          />
        </div>

        <div className="mt-8 flex items-center justify-between flex-wrap gap-2">
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted" data-testid="debate-room-footer">
            live · auto-refresh every 5s · {args.length} arguments · {debate?.vote_count || 0} votes
          </div>
          <Link className="text-xs font-mono text-amber hover:underline" to={`/debates/${slug}/results`} data-testid="debate-room-results-link">
            View results →
          </Link>
        </div>
      </div>
    </div>
  );
}
