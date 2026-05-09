import { useEffect, useRef, useState } from "react";
import { toPng } from "html-to-image";
import { toast } from "sonner";
import api from "../lib/api";

const MOODS = [
  { id: "funny", label: "Funny", emoji: "😂", from: "#F59E0B", to: "#7C3AED" },
  { id: "deep", label: "Deep", emoji: "💭", from: "#7C3AED", to: "#0EA5E9" },
  { id: "savage", label: "Savage", emoji: "🔥", from: "#F43F5E", to: "#F59E0B" },
  { id: "quote", label: "Quote", emoji: "✨", from: "#10B981", to: "#7C3AED" },
];

function avatarSrcOf(c) {
  if (!c?.avatar_url) return null;
  return c.avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${c.avatar_url}` : c.avatar_url;
}

export default function ShareCardModal({ open, onClose, clone, message, visitorMessage }) {
  const [mood, setMood] = useState("quote");
  const [generating, setGenerating] = useState(false);
  const cardRef = useRef(null);

  useEffect(() => {
    if (open) {
      // Fire analytics
      api.post("/analytics/event", { event_name: "share_card_opened", clone_id: clone?.clone_id, metadata: { mood } }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const moodObj = MOODS.find((m) => m.id === mood) || MOODS[0];
  const avatarSrc = avatarSrcOf(clone);

  const generatePng = async () => {
    if (!cardRef.current) return null;
    return await toPng(cardRef.current, {
      cacheBust: true,
      pixelRatio: 2,
      backgroundColor: "#070A12",
      style: { transform: "none" },
    });
  };

  const handleDownload = async () => {
    setGenerating(true);
    try {
      const dataUrl = await generatePng();
      if (!dataUrl) return;
      const link = document.createElement("a");
      link.download = `cloneme-${clone?.slug || "share"}-${Date.now()}.png`;
      link.href = dataUrl;
      link.click();
      toast.success("Image downloaded! Post it everywhere.");
      api.post("/analytics/event", { event_name: "share_card_downloaded", clone_id: clone?.clone_id, metadata: { mood } }).catch(() => {});
    } catch (e) {
      console.error(e);
      toast.error("Couldn't generate image. Try again.");
    } finally {
      setGenerating(false);
    }
  };

  const handleCopy = async () => {
    setGenerating(true);
    try {
      const dataUrl = await generatePng();
      if (!dataUrl) return;
      const blob = await (await fetch(dataUrl)).blob();
      if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        toast.success("Copied to clipboard! Paste in any chat.");
      } else {
        // Fallback: copy URL
        await navigator.clipboard.writeText(window.location.href);
        toast.success("Image copy not supported here — link copied instead.");
      }
      api.post("/analytics/event", { event_name: "share_card_copied", clone_id: clone?.clone_id, metadata: { mood } }).catch(() => {});
    } catch (e) {
      console.error(e);
      toast.error("Copy failed. Try Download instead.");
    } finally {
      setGenerating(false);
    }
  };

  const shareToX = () => {
    const url = window.location.href;
    const text = `Talk to my AI clone — ${clone?.display_name || "CloneMe"} 👇`;
    window.open(`https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}`, "_blank");
    api.post("/analytics/event", { event_name: "share_link_clicked", clone_id: clone?.clone_id, metadata: { channel: "x" } }).catch(() => {});
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-up" data-testid="share-card-modal" onClick={onClose}>
      <div className="glass-card-strong w-full max-w-3xl max-h-[92vh] overflow-y-auto p-5 md:p-7 animate-pop-in" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-5 gap-3">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted mb-1">SHARE CARD</p>
            <h3 className="heading-display text-2xl">Make this conversation viral.</h3>
          </div>
          <button onClick={onClose} className="btn-ghost text-xs px-3 py-1.5" data-testid="share-modal-close">Close ✕</button>
        </div>

        {/* Mood toggle */}
        <div className="flex flex-wrap gap-2 mb-5" data-testid="share-mood-tabs">
          {MOODS.map((m) => (
            <button
              key={m.id}
              onClick={() => setMood(m.id)}
              className={`tag transition ${mood === m.id ? "tag-amber border-amber/55 text-amber-soft scale-105" : ""}`}
              data-testid={`mood-${m.id}`}
            >
              {m.emoji} {m.label}
            </button>
          ))}
        </div>

        {/* PREVIEW CARD — what gets exported */}
        <div className="flex justify-center mb-5">
          <div
            ref={cardRef}
            className="relative w-[360px] h-[450px] rounded-3xl overflow-hidden"
            style={{
              background: `linear-gradient(160deg, ${moodObj.from}22 0%, #070A12 35%, #05070D 100%)`,
              fontFamily: "'Outfit', sans-serif",
            }}
            data-testid="share-card-preview"
          >
            {/* Decorative gradient orb */}
            <div
              style={{
                position: "absolute", top: -80, right: -60,
                width: 240, height: 240, borderRadius: "50%",
                background: `radial-gradient(circle, ${moodObj.from}, transparent 65%)`,
                filter: "blur(40px)",
                opacity: 0.7,
              }}
            />
            <div
              style={{
                position: "absolute", bottom: -120, left: -60,
                width: 280, height: 280, borderRadius: "50%",
                background: `radial-gradient(circle, ${moodObj.to}, transparent 65%)`,
                filter: "blur(50px)",
                opacity: 0.55,
              }}
            />
            {/* Subtle dot pattern */}
            <div style={{
              position: "absolute", inset: 0,
              backgroundImage: "radial-gradient(rgba(255,255,255,0.05) 1px, transparent 1px)",
              backgroundSize: "20px 20px",
            }} />

            {/* Top: avatar + clone name */}
            <div style={{ position: "relative", padding: "24px 26px 0" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                {avatarSrc ? (
                  <img src={avatarSrc} crossOrigin="anonymous" alt="" style={{ width: 50, height: 50, borderRadius: "50%", border: "2px solid rgba(255,255,255,0.2)", objectFit: "cover" }} />
                ) : (
                  <div style={{
                    width: 50, height: 50, borderRadius: "50%",
                    background: `linear-gradient(135deg, ${moodObj.from}, ${moodObj.to})`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    color: "#070A12", fontWeight: 900, fontSize: 22, border: "2px solid rgba(255,255,255,0.18)",
                  }}>
                    {clone?.display_name?.[0]?.toUpperCase() || "C"}
                  </div>
                )}
                <div style={{ minWidth: 0 }}>
                  <div style={{ color: "#fff", fontWeight: 800, fontSize: 16, lineHeight: 1.1, letterSpacing: "-0.01em" }}>{clone?.display_name || "CloneMe"}</div>
                  <div style={{ color: "rgba(255,255,255,0.55)", fontSize: 11, fontFamily: "'JetBrains Mono', monospace", marginTop: 2 }}>cloneme.ai/{clone?.slug || "you"}</div>
                </div>
                <div style={{
                  marginLeft: "auto",
                  fontSize: 9, fontWeight: 700, letterSpacing: "0.15em", textTransform: "uppercase",
                  color: "#FCD34D",
                  background: "rgba(245,158,11,0.15)",
                  border: "1px solid rgba(245,158,11,0.4)",
                  padding: "4px 8px", borderRadius: 999,
                }}>
                  AI Clone
                </div>
              </div>
            </div>

            {/* Visitor question (if exists) */}
            {visitorMessage && (
              <div style={{ position: "relative", padding: "20px 26px 0" }}>
                <div style={{
                  fontSize: 11, fontWeight: 700, letterSpacing: "0.15em", textTransform: "uppercase",
                  color: "rgba(255,255,255,0.45)", marginBottom: 6,
                }}>
                  Q
                </div>
                <div style={{ color: "rgba(255,255,255,0.78)", fontSize: 14, lineHeight: 1.4, fontWeight: 500 }}>
                  {(visitorMessage.length > 90 ? visitorMessage.slice(0, 90) + "…" : visitorMessage)}
                </div>
              </div>
            )}

            {/* Reply quote */}
            <div style={{ position: "relative", padding: "18px 26px 0" }}>
              <div style={{
                color: moodObj.from, fontSize: 36, lineHeight: 0.5, fontWeight: 900,
                marginBottom: 8,
              }}>"</div>
              <div style={{
                color: "#fff",
                fontSize: message?.length > 140 ? 18 : message?.length > 70 ? 22 : 26,
                lineHeight: 1.25,
                fontWeight: 700,
                letterSpacing: "-0.01em",
              }}>
                {message?.length > 220 ? message.slice(0, 220) + "…" : (message || "Add a reply to share")}
              </div>
            </div>

            {/* Footer: branding + mood */}
            <div style={{
              position: "absolute", bottom: 0, left: 0, right: 0,
              padding: "16px 26px",
              background: "linear-gradient(0deg, rgba(0,0,0,0.55), transparent)",
              display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 10,
            }}>
              <div>
                <div style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: "0.18em", textTransform: "uppercase",
                  color: "rgba(255,255,255,0.45)", marginBottom: 4,
                }}>
                  Talk to my AI clone
                </div>
                <div style={{ color: "#fff", fontWeight: 900, fontSize: 16, letterSpacing: "-0.02em" }}>
                  cloneme.ai<span style={{ color: moodObj.from }}>/</span>{clone?.slug || "you"}
                </div>
              </div>
              <div style={{
                fontSize: 11, fontWeight: 800,
                color: "#070A12",
                background: `linear-gradient(135deg, ${moodObj.from}, ${moodObj.to})`,
                padding: "5px 10px", borderRadius: 999,
              }}>
                {moodObj.emoji} {moodObj.label}
              </div>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          <button onClick={handleDownload} disabled={generating} className="btn-brutal w-full" data-testid="share-download-btn">
            {generating ? "Cooking…" : "↓ Download PNG"}
          </button>
          <button onClick={handleCopy} disabled={generating} className="btn-violet w-full" data-testid="share-copy-btn">
            ⎘ Copy image
          </button>
          <button onClick={shareToX} className="btn-ghost w-full" data-testid="share-x-btn">
            Post to X →
          </button>
        </div>
        <p className="text-[11px] text-muted mt-3 text-center font-mono uppercase tracking-widest">Optimized for Stories • DMs • Posts</p>
      </div>
    </div>
  );
}
