export default function PersonalitySlider({ label, leftLabel, rightLabel, value, onChange, testId }) {
  return (
    <div className="space-y-2" data-testid={testId}>
      <div className="flex items-center justify-between">
        <span className="label-brutal">{label}</span>
        <span className="font-mono text-xs">{value}/10</span>
      </div>
      <input
        type="range"
        min={0}
        max={10}
        step={1}
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="range-brutal"
      />
      <div className="flex items-center justify-between text-[11px] font-display font-bold uppercase tracking-wider text-muted">
        <span>← {leftLabel}</span>
        <span>{rightLabel} →</span>
      </div>
    </div>
  );
}
