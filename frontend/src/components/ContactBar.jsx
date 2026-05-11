/**
 * ContactBar — a minimal strip rendered directly under the Navbar on the
 * landing page. Surfaces the two support emails as mailto links plus a
 * succinct safety reminder.
 *
 * Rules:
 * - Must not break navbar layout (rendered as its own row).
 * - Mobile (≤640px): stacks vertically and hides the safety line to keep
 *   the strip compact; emails remain visible.
 * - Dark premium aesthetic preserved; uses existing token colors.
 * - No horizontal overflow.
 */
export default function ContactBar() {
  return (
    <div className="border-b border-white/5 bg-bg/50 backdrop-blur-md" data-testid="contact-bar">
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-2 sm:py-2.5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1.5 sm:gap-3">
        <p className="text-[11px] sm:text-xs font-mono uppercase tracking-widest text-muted hidden sm:block" data-testid="contact-bar-tagline">
          Original AI personas only. Use only content you own or have rights to use.
        </p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] sm:text-xs font-mono">
          <span className="text-muted uppercase tracking-widest">Contact</span>
          <a
            href="mailto:admin@aiclonechats.com"
            className="text-amber hover:text-amber-soft underline underline-offset-2 break-all"
            data-testid="contact-bar-admin-email"
          >
            admin@aiclonechats.com
          </a>
          <span className="text-white/15 hidden sm:inline" aria-hidden>·</span>
          <a
            href="mailto:krajapraveen@aiclonechats.com"
            className="text-amber hover:text-amber-soft underline underline-offset-2 break-all"
            data-testid="contact-bar-founder-email"
          >
            krajapraveen@aiclonechats.com
          </a>
        </div>
      </div>
    </div>
  );
}
