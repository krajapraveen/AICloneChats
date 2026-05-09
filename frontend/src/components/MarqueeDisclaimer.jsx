export default function MarqueeDisclaimer() {
  const items = Array(6).fill("⚠ THIS IS AN AI CLONE • NOT THE REAL PERSON • RESPONSES ARE AI-GENERATED");
  const content = items.join("   ★   ");
  return (
    <div className="bg-ink text-lemon py-2.5 overflow-hidden border-y-2 border-ink" data-testid="ai-disclaimer-marquee">
      <div className="marquee-track font-mono text-xs sm:text-sm uppercase tracking-[0.18em]">
        <span>{content}</span>
        <span>{content}</span>
      </div>
    </div>
  );
}
