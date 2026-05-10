/**
 * Debate Results — winner card + top arguments per side + share link.
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";

function copy(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text);
  }
  // fallback
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (_) { /* noop */ }
  ta.remove();
  return Promise.resolve();
}

function TopArg({ a, side }) {
  const tone = side === "A" ? "emerald" : "rose";
  return (
    <div className="brutal-card p-4" data-testid={`results-arg-${a.argument_id}`}>
      <div className="flex items-center justify-between mb-2">
        <span className={`text-[10px] font-mono uppercase tracking-widest text-${tone}-300`}>{a.anonymous_handle}</span>
        <span className="text-[10px] font-mono text-muted">AI {a.ai_score} · ▲ {a.upvotes} · ▼ {a.downvotes}</span>
      </div>
      <p className="text-sm text-ink/85 leading-relaxed whitespace-pre-wrap break-words">{a.content_preview}</p>
      <div className="text-[10px] font-mono text-muted mt-2">rank {a.rank_score}</div>
    </div>
  );
}

export default function DebateResults() {
  const { slug } = useParams();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.post("/analytics/event", { event_name: "debate_results_viewed", metadata: { slug, experience_variant: "debate_v1" } }).catch(() => {});
    api.get(`/debates/${slug}/results`).then((r) => setData(r.data)).catch((e) => setErr(e?.response?.data?.detail || "Could not load results."));
  }, [slug]);

  if (err) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 py-10">
          <div className="brutal-card p-8 text-center" data-testid="debate-results-error">
            <h1 className="heading-display text-2xl mb-2">{err}</h1>
            <Link to="/debates" className="btn-ghost mt-4 inline-block">← All debates</Link>
          </div>
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm" data-testid="debate-results-loading">loading…</div></div>;
  }

  const winnerLabel = data.winner_label || (data.leading_side ? (data.leading_side === "A" ? data.sides?.A?.label : data.sides?.B?.label) : "Tie");
  const winnerSide = data.winner_side || data.leading_side;
  const sideTone = winnerSide === "A" ? "emerald" : winnerSide === "B" ? "rose" : "amber";

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="debate-results-page">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <Link to={`/debates/${slug}`} className="text-xs font-mono text-muted hover:text-ink mb-3 inline-block" data-testid="debate-results-back">← Back to debate</Link>

        <div className="brutal-card p-6 sm:p-8 mb-8 text-center" data-testid="debate-results-winner-card">
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{data.ended ? "FINAL RESULT" : "CURRENTLY LEADING"}</div>
          <h1 className="heading-display text-3xl sm:text-5xl mt-2">{data.title}</h1>
          <div className={`mt-5 inline-flex items-center gap-3 px-5 py-2.5 rounded-full bg-${sideTone}-500/15 border border-${sideTone}-400/30`}>
            <span className={`text-xs font-mono uppercase tracking-widest text-${sideTone}-300`}>{winnerSide ? `Side ${winnerSide}` : "Tie"}</span>
            <span className="font-display font-black text-xl text-ink" data-testid="debate-results-winner-label">{winnerLabel}</span>
          </div>
          <div className="mt-6 flex items-center justify-center gap-3 flex-wrap">
            <button
              onClick={async () => {
                const url = window.location.href;
                await copy(`${data.title} — winner: ${winnerLabel}\n${url}`);
                toast.success("Copied result");
                api.post("/analytics/event", { event_name: "debate_result_shared", metadata: { slug, experience_variant: "debate_v1" } }).catch(() => {});
              }}
              className="btn-brutal text-sm"
              data-testid="debate-results-share-btn"
            >
              Copy share link
            </button>
            <Link to={`/debates/${slug}`} className="btn-ghost text-sm" data-testid="debate-results-keep-arguing">Keep arguing →</Link>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {["A", "B"].map((side) => (
            <div key={side} data-testid={`debate-results-side-${side}`}>
              <div className="flex items-baseline justify-between mb-3">
                <span className={`text-xs font-mono uppercase tracking-widest text-${side === "A" ? "emerald" : "rose"}-300`}>SIDE {side} · {data.sides?.[side]?.label}</span>
                <span className="text-[11px] font-mono text-muted">score {data.sides?.[side]?.side_score} · {data.sides?.[side]?.participants} debaters</span>
              </div>
              <div className="space-y-3">
                {(data.sides?.[side]?.top_arguments || []).map((a) => (
                  <TopArg key={a.argument_id} a={a} side={side} />
                ))}
                {(data.sides?.[side]?.top_arguments || []).length === 0 && (
                  <div className="brutal-card p-6 text-center text-xs font-mono text-muted">No arguments yet on this side.</div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
