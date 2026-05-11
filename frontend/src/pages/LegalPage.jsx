import { Link } from "react-router-dom";
import Navbar from "../components/Navbar";

/**
 * LegalPage — shared shell for Terms / Privacy / Acceptable Use pages.
 *
 * Intentionally plain: this page exists for legal clarity, not visual flourish.
 * The dark premium aesthetic is preserved; content is left-aligned and reads
 * top-to-bottom like a document.
 */
export default function LegalPage({ title, updated, children, testId }) {
  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[300px] h-[300px] -top-20 -right-20 opacity-20 animate-orb" aria-hidden />
      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-10 sm:py-16 relative" data-testid={testId}>
        <Link to="/" className="text-[11px] font-mono uppercase tracking-widest text-amber hover:text-amber-soft mb-6 inline-block">
          ← Back to home
        </Link>
        <h1 className="heading-display text-3xl sm:text-4xl md:text-5xl mb-2" data-testid={`${testId}-title`}>{title}</h1>
        {updated && (
          <p className="text-[11px] font-mono uppercase tracking-widest text-muted mb-8" data-testid={`${testId}-updated`}>
            Last updated · {updated}
          </p>
        )}
        <div className="legal-prose space-y-6 text-sm sm:text-base text-ink/85 leading-relaxed">
          {children}
        </div>
        <div className="border-t border-white/5 mt-12 pt-6 text-sm text-muted">
          Questions? Email{" "}
          <a href="mailto:admin@aiclonechats.com" className="text-amber underline underline-offset-2" data-testid={`${testId}-admin-mail`}>admin@aiclonechats.com</a>
          {" "}or{" "}
          <a href="mailto:krajapraveen@aiclonechats.com" className="text-amber underline underline-offset-2" data-testid={`${testId}-founder-mail`}>krajapraveen@aiclonechats.com</a>.
        </div>
      </div>
    </div>
  );
}
