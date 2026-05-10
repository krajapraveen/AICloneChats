import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { copyToClipboard } from "../lib/clipboard";

const TONE_LABELS = {
  concise: "Concise",
  professional: "Professional",
  friendly: "Friendly",
  apology: "Apology",
  dating: "Dating",
  negotiation: "Negotiation",
};

export default function VoiceSharePublic() {
  const { shareId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get(`/voice/share/${shareId}`).then((r) => {
      setData(r.data);
      // OG-friendly title update
      if (typeof document !== "undefined") {
        document.title = `Voice → Message · ${TONE_LABELS[r.data.tone] || ""} · aiclonechats.com`;
      }
    }).catch((e) => {
      setError(e?.response?.status === 404 ? "This share link doesn't exist or was deleted." : "Could not load share.");
    }).finally(() => setLoading(false));
  }, [shareId]);

  async function copyShareUrl() {
    const url = `${window.location.origin}/v/${shareId}`;
    const ok = await copyToClipboard(url);
    if (ok) toast.success("Link copied");
    else toast.error("Copy failed");
  }

  if (loading) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center px-4">
        <div className="brutal-card p-8 max-w-md text-center" data-testid="voice-share-error">
          <h1 className="heading-display text-2xl mb-2">Not found</h1>
          <p className="text-sm text-ink/70 mb-5">{error}</p>
          <Link to="/voice" className="btn-brutal text-sm inline-block">Try the studio →</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <div className="orb orb-emerald w-[420px] h-[420px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[380px] h-[380px] top-72 -left-32 opacity-20 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-8 sm:py-12 relative" data-testid="voice-share-page">
        {/* Top brand */}
        <div className="flex items-center justify-between mb-8">
          <Link to="/" className="flex items-center gap-2 group" data-testid="voice-share-logo">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-sm">C</div>
            <span className="font-display font-black text-base text-ink">aiclonechats<span className="text-amber">.</span>com</span>
          </Link>
          <button onClick={copyShareUrl} className="btn-ghost text-xs" data-testid="voice-share-copy-link">Copy link</button>
        </div>

        <div className="mb-6">
          <span className="tag tag-emerald mb-2 inline-block" data-testid="voice-share-tone">{(TONE_LABELS[data.tone] || data.tone || "").toUpperCase()}</span>
          <h1 className="heading-display text-2xl sm:text-3xl md:text-4xl leading-tight">From messy thought → clear message</h1>
        </div>

        {/* Side-by-side */}
        <div className="grid sm:grid-cols-2 gap-3 sm:gap-4">
          <div className="brutal-card p-4 sm:p-5" data-testid="voice-share-raw">
            <span className="text-xs font-mono uppercase tracking-widest text-muted block mb-2">What I said</span>
            <p className="text-base text-ink/80 leading-relaxed whitespace-pre-wrap italic">{data.raw_input || "—"}</p>
          </div>
          <div className="brutal-card p-4 sm:p-5 border-emerald" data-testid="voice-share-polished">
            <span className="text-xs font-mono uppercase tracking-widest text-emerald-soft block mb-2">What we sent</span>
            <p className="text-base text-ink leading-relaxed whitespace-pre-wrap font-medium">{data.polished_message || "—"}</p>
          </div>
        </div>

        {Array.isArray(data.redacted_categories) && data.redacted_categories.length > 0 && (
          <p className="text-xs text-muted mt-3 font-mono" data-testid="voice-share-redacted-note">
            Privacy: redacted {data.redacted_categories.join(", ")}.
          </p>
        )}

        {/* CTA + watermark */}
        <div className="mt-10 brutal-card p-5 sm:p-6 text-center" data-testid="voice-share-cta">
          <h2 className="heading-display text-xl sm:text-2xl mb-2">Say what you mean — clearly.</h2>
          <p className="text-sm text-ink/70 mb-4">Try it free. No signup needed.</p>
          <Link to="/voice" className="btn-brutal text-sm inline-block" data-testid="voice-share-cta-btn">
            Open the studio →
          </Link>
        </div>

        <div className="mt-8 text-center text-xs font-mono uppercase tracking-widest text-muted/70">
          {data.watermark || "Optimized with aiclonechats.com Voice"}
        </div>
      </div>
    </div>
  );
}
