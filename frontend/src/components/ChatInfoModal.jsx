import { useEffect } from "react";
import api from "../lib/api";

export default function ChatInfoModal({ open, onClose, info }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    api.post("/analytics/event", { event_name: "chat_info_opened", metadata: { chat_type: info?.id } }).catch(() => {});
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, info?.id, onClose]);

  if (!open || !info) return null;

  const handleClose = () => {
    api.post("/analytics/event", { event_name: "chat_info_closed", metadata: { chat_type: info?.id } }).catch(() => {});
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/70 backdrop-blur-sm animate-fade-up safe-px"
      onClick={handleClose}
      data-testid={`chat-info-modal-${info.id}`}
    >
      <div
        className="glass-card-strong modal-shell w-full sm:max-w-xl p-6 sm:p-8 rounded-t-3xl sm:rounded-3xl animate-pop-in chat-form-sticky"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 mb-5">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted mb-1">{info.kicker}</p>
            <h3 className="heading-display text-2xl sm:text-3xl">{info.title}</h3>
          </div>
          <button onClick={handleClose} className="btn-ghost text-xs px-3 py-1.5 flex-shrink-0" data-testid={`chat-info-close-${info.id}`}>
            Close ✕
          </button>
        </div>

        <p className="text-sm leading-relaxed text-ink/85 font-medium mb-6">{info.body}</p>

        <div className="space-y-5">
          <section>
            <p className="label-brutal mb-2">How to use</p>
            <ol className="space-y-1.5 text-sm text-ink/80 list-decimal list-inside font-medium">
              {info.how_to.map((step, i) => <li key={i}>{step}</li>)}
            </ol>
          </section>

          <section className="grid grid-cols-1 gap-3">
            <div className="rounded-xl border border-white/10 bg-white/5 p-4">
              <p className="label-brutal mb-2">Example input</p>
              <p className="text-sm text-ink/80 italic">"{info.example.input}"</p>
            </div>
            <div className="rounded-xl border border-amber/30 bg-amber/8 p-4">
              <p className="label-brutal mb-2 text-amber-soft">Example output</p>
              <p className="text-sm text-ink/90">"{info.example.output}"</p>
            </div>
          </section>

          <section className="rounded-xl border border-rose/30 bg-rose/8 p-4">
            <p className="label-brutal mb-2 text-rose-soft">Safety note</p>
            <p className="text-xs text-ink/80 leading-relaxed">{info.safety}</p>
          </section>

          {info.cta && (
            <div className="pt-2">
              <button onClick={() => { info.cta.onClick(); handleClose(); }} className="btn-brutal w-full" data-testid={`chat-info-cta-${info.id}`}>
                {info.cta.label}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
