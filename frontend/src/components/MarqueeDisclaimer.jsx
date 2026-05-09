export default function MarqueeDisclaimer() {
  const items = Array(6).fill("THIS IS AN AI CLONE • NOT THE REAL PERSON • RESPONSES ARE AI-GENERATED");
  const content = items.join("   ◆   ");
  return (
    <div className="bg-amber/10 text-amber-soft py-2 overflow-hidden border-y border-amber/25 backdrop-blur-md" data-testid="ai-disclaimer-marquee">
      <div className="marquee-track font-mono text-[11px] sm:text-xs uppercase tracking-[0.18em]">
        <span>{content}</span>
        <span>{content}</span>
      </div>
    </div>
  );
}
