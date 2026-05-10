import { useState } from "react";

/**
 * Explicit, off-by-default share confirmation.
 * Trust matters — we operate on intimate communication data.
 */
export default function VoiceShareConfirm({ open, onClose, onConfirm, busy }) {
  const [ack, setAck] = useState(false);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/75 backdrop-blur-sm p-0 sm:p-4 safe-px"
      onClick={onClose}
      data-testid="voice-share-confirm-modal"
    >
      <div
        className="brutal-card modal-shell w-full sm:max-w-md p-6 sm:p-7 relative rounded-t-3xl sm:rounded-3xl chat-form-sticky"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <span className="tag tag-amber">CREATE PUBLIC LINK</span>
          <button onClick={onClose} className="text-muted hover:text-ink text-xl leading-none" aria-label="Close" data-testid="voice-share-confirm-close">×</button>
        </div>
        <h3 className="heading-display text-2xl mb-2">This creates a public link.</h3>
        <p className="text-sm font-medium text-ink/75 leading-relaxed mb-4">
          Anyone with the link will be able to see your input and the polished message side-by-side.
          We'll automatically redact phone numbers, emails, OTPs, addresses, and links — but please double-check before sharing.
        </p>
        <ul className="space-y-1.5 text-xs text-ink/70 mb-5">
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> Link is read-only — viewers can't edit, comment, or message you</li>
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> No likes. No feed. No profile pages.</li>
          <li className="flex items-start gap-2"><span className="text-emerald mt-0.5">●</span> You can delete the link anytime</li>
        </ul>
        <label className="flex items-start gap-2 mb-5 text-sm text-ink/85 cursor-pointer">
          <input
            type="checkbox"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
            className="mt-1 accent-emerald"
            data-testid="voice-share-ack-checkbox"
          />
          <span>I understand this creates a public link anyone can view.</span>
        </label>
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            type="button"
            onClick={() => onConfirm && onConfirm()}
            disabled={!ack || busy}
            className="btn-brutal flex-1 disabled:opacity-50"
            data-testid="voice-share-confirm-btn"
          >
            {busy ? "Creating link…" : "Create share link →"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost flex-1"
            data-testid="voice-share-cancel-btn"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
