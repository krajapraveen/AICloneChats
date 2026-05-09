export default function MoodSignalPill({ moodUI, theme }) {
  if (!moodUI?.enabled || !moodUI?.show_mood_pill || !moodUI?.microcopy) {
    return null;
  }
  const t = theme || {};
  return (
    <div
      className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-display font-bold uppercase tracking-[0.16em] backdrop-blur animate-fade-up"
      style={{
        background: t.pillBg,
        border: `1px solid ${t.pillBorder}`,
        color: t.pillText,
      }}
      data-testid="mood-signal-pill"
      data-mood-state={moodUI.dominant_state}
      title={`Detected: ${moodUI.dominant_state} (${Math.round((moodUI.confidence || 0) * 100)}%)`}
    >
      <span aria-hidden>✨</span>
      <span>{moodUI.microcopy}</span>
    </div>
  );
}
