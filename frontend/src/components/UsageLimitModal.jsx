import { Link } from "react-router-dom";

export default function UsageLimitModal({ open, onClose, onUpgradeClick, daily_limit = 5 }) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
      data-testid="usage-limit-modal"
    >
      <div
        className="brutal-card max-w-md w-full p-7 relative"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <span className="tag tag-amber">DAILY LIMIT</span>
          <button
            onClick={onClose}
            className="text-muted hover:text-ink text-xl leading-none"
            aria-label="Close"
            data-testid="usage-limit-close"
          >
            ×
          </button>
        </div>
        <h3 className="heading-display text-2xl mb-2">You've used your {daily_limit} free replies today.</h3>
        <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
          Upgrade to Pro for unlimited replies, advanced tones, saved history, and negotiation mode.
        </p>
        <ul className="space-y-2 text-sm text-ink/80 mb-6">
          <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Unlimited daily replies</li>
          <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Saved history + favorites</li>
          <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Advanced tone library</li>
          <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Negotiation mode</li>
        </ul>
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            onClick={onUpgradeClick}
            className="btn-brutal flex-1"
            data-testid="usage-limit-upgrade-btn"
          >
            Upgrade to Pro →
          </button>
          <Link
            to="/smart-reply/history"
            className="btn-ghost text-center flex-1"
            data-testid="usage-limit-history-link"
          >
            View past replies
          </Link>
        </div>
        <p className="text-xs text-muted mt-4 font-mono uppercase tracking-widest text-center">
          Resets at midnight UTC
        </p>
      </div>
    </div>
  );
}
