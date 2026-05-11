import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";
import UsageLimitModal from "../components/UsageLimitModal";

const MODES = [
  { id: "dating", label: "Dating", desc: "Chemistry, never coercive" },
  { id: "professional", label: "Professional", desc: "Clear and decisive" },
  { id: "apology", label: "Apology", desc: "Own it, no excuses" },
  { id: "negotiation", label: "Negotiation", desc: "Firm and value-anchored" },
];
const TONES = ["warm", "calm", "flirty", "professional", "confident", "direct"];

const LABEL_TAGS = {
  safe: { tag: "tag-emerald", title: "Safe" },
  warm: { tag: "tag-amber", title: "Warm" },
  confident: { tag: "tag-violet", title: "Confident" },
};
const LENGTH_TAG = {
  short: "SHORT",
  medium: "MEDIUM",
  long: "LONG",
};
const RISK_COLOR = { low: "tag-emerald", medium: "tag-amber", high: "tag-rose" };

function track(event_name, metadata) {
  api.post("/smart-reply/track", { event_name, metadata: metadata || {} }).catch(() => {});
}

export default function SmartReplyStudio() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [incoming, setIncoming] = useState("");
  const [mode, setMode] = useState("dating");
  const [tone, setTone] = useState("warm");
  const [relationshipContext, setRelationshipContext] = useState("");
  const [userGoal, setUserGoal] = useState("");
  const [whatIWantToSay, setWhatIWantToSay] = useState("");
  const [showOptional, setShowOptional] = useState(false);
  const [generating, setGenerating] = useState(false);

  const [result, setResult] = useState(null); // {session_id, replies, tone_explanation, risk_warning}
  const [favIndexes, setFavIndexes] = useState(new Set());
  const [favIds, setFavIds] = useState({}); // index -> favorite_id
  const [paywallOpen, setPaywallOpen] = useState(false);
  const [usage, setUsage] = useState(null);

  const resultRef = useRef(null);
  const pasteTrackedRef = useRef(false);

  // Redirect to login if not authed
  useEffect(() => {
    if (!authLoading && !user) {
      navigate("/login?redirect=/smart-reply");
    }
  }, [user, authLoading, navigate]);

  useEffect(() => {
    track("smart_reply_page_opened");
    api.get("/smart-reply/subscription/status").then((r) => setUsage(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (incoming && !pasteTrackedRef.current) {
      pasteTrackedRef.current = true;
      track("smart_reply_paste_started", { mode });
    }
  }, [incoming, mode]);

  const generate = async () => {
    if (!incoming.trim() || generating) return;
    setGenerating(true);
    try {
      const { data } = await api.post("/smart-reply/generate", {
        incoming_message: incoming.trim(),
        mode,
        desired_tone: tone,
        relationship_context: relationshipContext.trim(),
        user_goal: userGoal.trim(),
        what_i_want_to_say: whatIWantToSay.trim(),
      });
      setResult(data);
      setFavIndexes(new Set());
      setFavIds({});
      setUsage((u) => ({
        ...(u || {}),
        daily_used: data.is_pro ? 0 : (data.daily_remaining >= 0 ? 5 - data.daily_remaining : 0),
        daily_remaining: data.daily_remaining,
        is_pro: data.is_pro,
        subscription_status: data.is_pro ? "pro" : "free",
        daily_limit: data.is_pro ? null : 5,
      }));
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 402 || (typeof detail === "object" && detail?.code === "usage_limit_reached")) {
        track("smart_reply_paywall_opened", { mode });
        setPaywallOpen(true);
      } else {
        toast.error(typeof detail === "string" ? detail : "Couldn't generate. Try again.");
      }
    } finally {
      setGenerating(false);
    }
  };

  const regenerate = (idx) => {
    track("smart_reply_regenerate_clicked", { mode, reply_index: idx });
    generate();
  };

  const copyReply = async (text, idx) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied — paste it where you need it.");
      track("smart_reply_copy_clicked", { mode, reply_index: idx, session_id: result?.session_id });
    } catch {
      toast.error("Copy failed");
    }
  };

  const toggleFavorite = async (idx, replyText) => {
    if (!result?.session_id) return;
    if (favIndexes.has(idx)) {
      const favId = favIds[idx];
      if (!favId) return;
      try {
        await api.delete(`/smart-reply/favorites/${favId}`);
        const next = new Set(favIndexes);
        next.delete(idx);
        setFavIndexes(next);
        const nextIds = { ...favIds };
        delete nextIds[idx];
        setFavIds(nextIds);
        toast.success("Removed from favorites");
      } catch {
        toast.error("Could not remove favorite");
      }
      return;
    }
    try {
      const { data } = await api.post(`/smart-reply/${result.session_id}/favorite`, {
        reply_index: idx,
        reply_text: replyText,
      });
      setFavIndexes(new Set([...favIndexes, idx]));
      setFavIds({ ...favIds, [idx]: data.favorite_id });
      toast.success("Saved to favorites");
    } catch {
      toast.error("Could not save favorite");
    }
  };

  const handleUpgrade = () => {
    track("smart_reply_upgrade_clicked", { source: "usage_limit_modal" });
    setPaywallOpen(false);
    navigate("/pricing?source=smart_reply&intent=upgrade");
  };

  const remainingPill = () => {
    if (!usage) return null;
    if (usage.is_pro) return <span className="tag tag-violet" data-testid="sr-usage-pill">PRO · UNLIMITED</span>;
    const remaining = usage.daily_remaining ?? 5;
    const tone = remaining === 0 ? "tag-rose" : remaining <= 2 ? "tag-amber" : "tag-emerald";
    return (
      <span className={`tag ${tone}`} data-testid="sr-usage-pill">
        {remaining} / {usage.daily_limit ?? 5} left today
      </span>
    );
  };

  if (authLoading || !user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="orb orb-amber w-[420px] h-[420px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[380px] h-[380px] top-72 -left-32 opacity-20 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8 relative" data-testid="smart-reply-studio">
        {/* Header */}
        <div className="glass-card p-5 sm:p-6 mb-5">
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
            <div className="flex-1 min-w-0">
              <span className="tag tag-amber mb-2 inline-block">SMART REPLY</span>
              <h1 className="heading-display text-2xl sm:text-3xl md:text-4xl">Paste it. Get the right reply.</h1>
              <p className="text-sm font-medium text-ink/70 leading-relaxed mt-2">
                Drop in the message you got. Pick a mode + tone. We'll write 3 copy-ready replies.
              </p>
            </div>
            <div className="flex flex-row sm:flex-col items-start sm:items-end gap-2 flex-wrap">
              {remainingPill()}
              <div className="flex gap-2">
                <Link to="/smart-reply/history" className="text-xs font-mono uppercase tracking-widest text-muted hover:text-amber-soft" data-testid="link-sr-history">History</Link>
                <span className="text-muted text-xs">·</span>
                <Link to="/smart-reply/favorites" className="text-xs font-mono uppercase tracking-widest text-muted hover:text-amber-soft" data-testid="link-sr-favorites">Favorites</Link>
              </div>
            </div>
          </div>
        </div>

        {/* Input */}
        <div className="glass-card p-6 mb-5 space-y-5" data-testid="sr-input-block">
          <div>
            <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
              Incoming message
            </label>
            <textarea
              className="input-brutal w-full min-h-[120px] resize-y"
              placeholder="Paste the exact message you received…"
              value={incoming}
              onChange={(e) => setIncoming(e.target.value)}
              maxLength={2000}
              data-testid="sr-incoming-textarea"
            />
            <div className="text-right text-xs font-mono text-muted mt-1">{incoming.length}/2000</div>
          </div>

          <div>
            <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
              Mode
            </label>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid="sr-mode-grid">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setMode(m.id)}
                  className={`brutal-card p-3 text-left transition ${mode === m.id ? "ring-2 ring-amber" : "opacity-70 hover:opacity-100"}`}
                  data-testid={`sr-mode-${m.id}`}
                >
                  <div className="font-display font-bold text-sm">{m.label}</div>
                  <div className="text-[11px] text-muted leading-snug mt-0.5">{m.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
              Tone
            </label>
            <div className="flex flex-wrap gap-2" data-testid="sr-tone-row">
              {TONES.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTone(t)}
                  className={`tag ${tone === t ? "tag-violet" : "tag"} cursor-pointer`}
                  data-testid={`sr-tone-${t}`}
                >
                  {t.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          <button
            type="button"
            onClick={() => setShowOptional((s) => !s)}
            className="text-xs font-mono uppercase tracking-widest text-muted hover:text-amber-soft"
            data-testid="sr-toggle-optional"
          >
            {showOptional ? "− Hide context" : "+ Add context (optional)"}
          </button>

          {showOptional && (
            <div className="space-y-4" data-testid="sr-optional-block">
              <div>
                <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
                  Relationship context
                </label>
                <input
                  type="text"
                  className="input-brutal w-full"
                  placeholder="e.g. talking 2 weeks, they cancelled once before"
                  value={relationshipContext}
                  onChange={(e) => setRelationshipContext(e.target.value)}
                  maxLength={500}
                  data-testid="sr-relationship-input"
                />
              </div>
              <div>
                <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
                  Your goal
                </label>
                <input
                  type="text"
                  className="input-brutal w-full"
                  placeholder="e.g. confirm without sounding desperate"
                  value={userGoal}
                  onChange={(e) => setUserGoal(e.target.value)}
                  maxLength={300}
                  data-testid="sr-goal-input"
                />
              </div>
              <div>
                <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
                  What I want to say (rough)
                </label>
                <textarea
                  className="input-brutal w-full min-h-[80px] resize-y"
                  placeholder="Optional. Your raw thought — we'll polish it."
                  value={whatIWantToSay}
                  onChange={(e) => setWhatIWantToSay(e.target.value)}
                  maxLength={500}
                  data-testid="sr-rough-input"
                />
              </div>
            </div>
          )}

          <button
            type="button"
            onClick={generate}
            disabled={!incoming.trim() || generating}
            className="btn-brutal w-full text-base"
            data-testid="sr-generate-btn"
          >
            {generating ? "Generating…" : "Generate 3 replies →"}
          </button>
        </div>

        {/* Results */}
        {result && (
          <div ref={resultRef} className="space-y-4" data-testid="sr-results-block">
            {result.tone_explanation && (
              <div className="glass-card p-4 text-sm text-ink/80 leading-relaxed" data-testid="sr-tone-explanation">
                <span className="text-xs font-mono uppercase tracking-widest text-muted block mb-1">Tone applied</span>
                {result.tone_explanation}
              </div>
            )}
            {result.risk_warning && (
              <div className="brutal-card border-amber p-4 bg-amber/10" data-testid="sr-risk-warning">
                <div className="flex items-start gap-2">
                  <span className="tag tag-amber">RISK</span>
                  <p className="text-sm font-medium text-ink/85 leading-relaxed">{result.risk_warning}</p>
                </div>
              </div>
            )}

            {result.replies.map((r, idx) => {
              const meta = LABEL_TAGS[r.label] || LABEL_TAGS.safe;
              return (
                <div key={idx} className="brutal-card p-5" data-testid={`sr-reply-card-${idx}`}>
                  <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`tag ${meta.tag}`} data-testid={`sr-reply-label-${idx}`}>{meta.title.toUpperCase()}</span>
                      <span className="tag" data-testid={`sr-reply-length-${idx}`}>{LENGTH_TAG[r.length] || "MEDIUM"}</span>
                      <span className={`tag ${RISK_COLOR[r.risk_level] || "tag-emerald"}`} data-testid={`sr-reply-risk-${idx}`}>
                        RISK · {r.risk_level.toUpperCase()}
                      </span>
                    </div>
                  </div>
                  <p className="font-medium text-base text-ink leading-relaxed whitespace-pre-wrap" data-testid={`sr-reply-text-${idx}`}>
                    {r.reply}
                  </p>
                  {r.why_it_works && (
                    <p className="text-xs text-muted mt-3 italic leading-relaxed" data-testid={`sr-reply-why-${idx}`}>
                      {r.why_it_works}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2 mt-4">
                    <button
                      onClick={() => copyReply(r.reply, idx)}
                      className="btn-brutal text-sm"
                      data-testid={`sr-copy-btn-${idx}`}
                    >
                      Copy
                    </button>
                    <button
                      onClick={() => toggleFavorite(idx, r.reply)}
                      className={favIndexes.has(idx) ? "btn-violet text-sm" : "btn-ghost text-sm"}
                      data-testid={`sr-favorite-btn-${idx}`}
                    >
                      {favIndexes.has(idx) ? "★ Saved" : "☆ Save"}
                    </button>
                    <button
                      onClick={() => regenerate(idx)}
                      disabled={generating}
                      className="btn-ghost text-sm"
                      data-testid={`sr-regenerate-btn-${idx}`}
                    >
                      {generating ? "…" : "↻ Regenerate"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <UsageLimitModal
        open={paywallOpen}
        onClose={() => setPaywallOpen(false)}
        onUpgradeClick={handleUpgrade}
        daily_limit={5}
      />
    </div>
  );
}
