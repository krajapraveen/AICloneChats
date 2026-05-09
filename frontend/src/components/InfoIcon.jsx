export default function InfoIcon({ onClick, label = "More info", testId }) {
  return (
    <button
      type="button"
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClick && onClick(); }}
      className="inline-flex items-center justify-center w-6 h-6 rounded-full border border-white/20 bg-white/5 text-ink/70 hover:text-amber-soft hover:border-amber/50 hover:bg-amber/10 transition flex-shrink-0"
      aria-label={label}
      data-testid={testId}
      title={label}
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="16" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12.01" y2="8" />
      </svg>
    </button>
  );
}
