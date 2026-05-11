/**
 * ContactBar — minimal horizontal strip directly beneath the Navbar on /.
 *
 * Layout contract:
 *   Desktop (≥768px): one centered row, baseline-aligned —
 *     [Contact:] [admin@…] [krajapraveen@…] [· compliance tagline]
 *   Mobile (<768px):  stack vertically, left-aligned, no wrap collisions.
 *
 * Design rules baked in:
 *   - No tracking-widest / uppercase on emails or tagline (that broke wrapping
 *     last time — each character was 0.2em wider than expected).
 *   - "Contact" label is the only uppercase token; everything else reads
 *     as plain text.
 *   - One flex row, gap-x for spacing, gap-y for the mobile stack — no
 *     justify-between drift between left/right groups.
 *   - Inline-block separators only between items that won't ever wrap.
 */
export default function ContactBar() {
  return (
    <div className="border-b border-white/5 bg-bg/50 backdrop-blur-md" data-testid="contact-bar">
      <div
        className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-2 sm:py-2.5
                   flex flex-col md:flex-row md:items-center md:flex-wrap
                   gap-y-1 md:gap-y-0 gap-x-3 md:gap-x-4
                   text-[12px] sm:text-[13px] leading-snug"
      >
        <span
          className="font-mono text-[10px] sm:text-[11px] uppercase tracking-[0.18em] text-muted shrink-0"
          data-testid="contact-bar-label"
        >
          Contact
        </span>

        <a
          href="mailto:admin@aiclonechats.com"
          className="text-amber hover:text-amber-soft underline underline-offset-2 break-all md:break-normal"
          data-testid="contact-bar-admin-email"
        >
          admin@aiclonechats.com
        </a>

        <span className="hidden md:inline text-white/15" aria-hidden>·</span>

        <a
          href="mailto:krajapraveen@aiclonechats.com"
          className="text-amber hover:text-amber-soft underline underline-offset-2 break-all md:break-normal"
          data-testid="contact-bar-founder-email"
        >
          krajapraveen@aiclonechats.com
        </a>

        <span className="hidden md:inline text-white/15" aria-hidden>·</span>

        <p
          className="text-muted md:flex-1 md:text-right"
          data-testid="contact-bar-tagline"
        >
          Original AI personas only. Use only content you own or have rights to use.
        </p>
      </div>
    </div>
  );
}
