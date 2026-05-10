import { useState } from "react";
import { Link } from "react-router-dom";

export default function VoiceSignupWall({ open, onClose, anonRemaining = 0 }) {
  const [closing, setClosing] = useState(false);
  if (!open) return null;
  const handleClose = () => {
    setClosing(true);
    setTimeout(() => { onClose && onClose(); setClosing(false); }, 150);
  };
  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/75 backdrop-blur-sm p-0 sm:p-4 safe-px"
      onClick={handleClose}
      data-testid="voice-signup-wall"
    >
      <div
        className={`brutal-card modal-shell w-full sm:max-w-md p-6 sm:p-7 relative rounded-t-3xl sm:rounded-3xl chat-form-sticky transition-transform ${closing ? "translate-y-2" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <span className="tag tag-emerald">FREE TRIAL DONE</span>
          <button onClick={handleClose} className="text-muted hover:text-ink text-xl leading-none" aria-label="Close" data-testid="voice-wall-close">×</button>
        </div>
        <h3 className="heading-display text-2xl mb-2">You used your 3 free generations.</h3>
        <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
          Sign up free to keep going — 20 generations per day, history, and saved messages.
        </p>
        <ul className="space-y-2 text-sm text-ink/80 mb-6">
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> 20 free generations every day</li>
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> Full history of your messages</li>
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> One-tap refines: shorter, more confident, more polite</li>
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> Works with voice notes, uploads, and pasted text</li>
        </ul>
        <div className="flex flex-col sm:flex-row gap-2">
          <Link to="/register?redirect=/voice" className="btn-brutal flex-1 text-center" data-testid="voice-wall-signup-btn">
            Sign up free →
          </Link>
          <Link to="/login?redirect=/voice" className="btn-ghost text-center flex-1" data-testid="voice-wall-login-link">
            I have an account
          </Link>
        </div>
        <p className="text-xs text-muted mt-4 font-mono uppercase tracking-widest text-center">
          {anonRemaining > 0 ? `${anonRemaining} trials left` : "No card needed"}
        </p>
      </div>
    </div>
  );
}
