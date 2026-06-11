import { useEffect } from "react";
import { Link } from "react-router-dom";
import Navbar from "../components/Navbar";

/**
 * LegalPage — shared shell for all legal & compliance pages.
 *
 * Consistent header, eyebrow, "last updated", navigation, contact footer, and
 * a small set of typography helpers (LegalSection, LegalAlert, LegalTable).
 * The dark premium aesthetic is preserved; content is left-aligned and reads
 * top-to-bottom like a real legal document.
 */
export default function LegalPage({ title, eyebrow, updated, description, children, testId }) {
  useEffect(() => {
    if (title) document.title = `${title} · aiclonechats.com`;
    if (description) {
      let tag = document.querySelector('meta[name="description"]');
      if (!tag) {
        tag = document.createElement("meta");
        tag.setAttribute("name", "description");
        document.head.appendChild(tag);
      }
      tag.setAttribute("content", description);
    }
  }, [title, description]);

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[300px] h-[300px] -top-20 -right-20 opacity-20 animate-orb" aria-hidden />
      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-10 sm:py-16 relative" data-testid={testId}>
        <Link to="/" className="text-[11px] font-mono uppercase tracking-widest text-amber hover:text-amber-soft mb-6 inline-block" data-testid={`${testId}-back-home`}>
          ← Back to home
        </Link>

        {eyebrow && (
          <div className="text-[10px] font-mono uppercase tracking-[0.22em] text-amber/80 mb-3" data-testid={`${testId}-eyebrow`}>
            {eyebrow}
          </div>
        )}

        <h1 className="heading-display text-3xl sm:text-4xl md:text-5xl mb-2" data-testid={`${testId}-title`}>
          {title}
        </h1>

        {updated && (
          <p className="text-[11px] font-mono uppercase tracking-widest text-muted mb-8" data-testid={`${testId}-updated`}>
            Last updated · {updated}
          </p>
        )}

        <div className="legal-prose space-y-6 text-sm sm:text-base text-ink/85 leading-relaxed">
          {children}
        </div>

        {/* Cross-links between legal pages */}
        <nav className="border-t border-white/5 mt-12 pt-6" aria-label="Legal documents">
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-3">Related</div>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <Link to="/privacy-policy" className="text-ink/80 hover:text-amber underline-offset-2 hover:underline" data-testid={`${testId}-link-privacy`}>Privacy Policy</Link>
            <Link to="/terms-of-service" className="text-ink/80 hover:text-amber underline-offset-2 hover:underline" data-testid={`${testId}-link-terms`}>Terms of Service</Link>
            <Link to="/cookie-policy" className="text-ink/80 hover:text-amber underline-offset-2 hover:underline" data-testid={`${testId}-link-cookies`}>Cookie Policy</Link>
            <Link to="/privacy-settings" className="text-ink/80 hover:text-amber underline-offset-2 hover:underline" data-testid={`${testId}-link-privacy-settings`}>Privacy Settings</Link>
            <Link to="/security" className="text-ink/80 hover:text-amber underline-offset-2 hover:underline" data-testid={`${testId}-link-security`}>Security</Link>
            <Link to="/acceptable-use" className="text-ink/80 hover:text-amber underline-offset-2 hover:underline" data-testid={`${testId}-link-acceptable-use`}>Acceptable Use</Link>
          </div>
        </nav>

        <div className="border-t border-white/5 mt-8 pt-6 text-sm text-muted">
          Questions, requests, or concerns? Email{" "}
          <a href="mailto:admin@aiclonechats.com" className="text-amber underline underline-offset-2" data-testid={`${testId}-admin-mail`}>admin@aiclonechats.com</a>
          {" "}or{" "}
          <a href="mailto:krajapraveen@aiclonechats.com" className="text-amber underline underline-offset-2" data-testid={`${testId}-founder-mail`}>krajapraveen@aiclonechats.com</a>.
          {" "}For security-only reports, see our{" "}
          <Link to="/security" className="text-amber underline underline-offset-2" data-testid={`${testId}-security-link`}>Security</Link> page.
        </div>
      </div>
    </div>
  );
}

/** Section with a numbered heading, used by all legal pages. */
export function LegalSection({ id, number, title, children, testId }) {
  return (
    <section id={id} data-testid={testId}>
      <h2 className="heading-display text-xl sm:text-2xl mt-8 mb-3 scroll-mt-24">
        {number != null && <span className="text-amber/70 font-mono text-sm mr-2">{String(number).padStart(2, "0")}</span>}
        {title}
      </h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

/** Strong inline alert (used for AI/identity safety clauses). */
export function LegalAlert({ tone = "amber", title, children, testId }) {
  const palette = tone === "danger"
    ? "border-red-500/40 bg-red-500/10 text-red-200"
    : "border-amber/40 bg-amber-500/10 text-amber-100";
  return (
    <div className={`brutal-card p-4 my-4 border ${palette}`} role="note" data-testid={testId}>
      {title && (
        <div className="text-[10px] font-mono uppercase tracking-widest mb-1 opacity-90">
          {title}
        </div>
      )}
      <div className="text-sm leading-relaxed">{children}</div>
    </div>
  );
}

/** Simple two-column table used for cookies, retention windows, etc. */
export function LegalTable({ headers, rows, testId }) {
  return (
    <div className="overflow-x-auto my-4 brutal-card" data-testid={testId}>
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-[10px] font-mono uppercase tracking-widest text-amber/80 border-b border-white/10">
            {headers.map((h, i) => (
              <th key={i} className="py-3 px-4 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-white/5 last:border-0">
              {row.map((cell, ci) => (
                <td key={ci} className="py-3 px-4 align-top text-ink/85">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
