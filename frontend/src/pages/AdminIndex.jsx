/**
 * Admin index — categorized discovery surface for every operator tool.
 *
 * Routes are unchanged. This page is purely a directory: it gathers
 * the existing admin URLs into Moderation / Analytics / Operations
 * sections so operators can find what they need without the navbar
 * being a wall of violet links.
 *
 * RBAC: gated by role === "admin". Non-admins land on a 403 card.
 * The route-level guards on each downstream admin page remain intact
 * — this page is one more layer, not a replacement.
 */
import { Link, Navigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const SECTIONS = [
  {
    id: "moderation",
    kicker: "MODERATION",
    title: "Keep the rooms safe.",
    description: "Human-in-the-loop review surfaces for live user content. Every action here is audited.",
    tools: [
      {
        to: "/admin/anonymous-reality",
        name: "Anonymous Moderation",
        blurb: "Review reports, hide rooms, manage escalations across all anonymous topic rooms.",
        testId: "admin-card-anonymous-mod",
      },
      {
        to: "/admin/debates",
        name: "Debates Moderation",
        blurb: "Review and hide reported debate arguments. Track moderation actions per debate room.",
        testId: "admin-card-debates-mod",
      },
      {
        to: "/admin/safety",
        name: "Safety Center",
        blurb: "Centralized safety-filter event log: blocks, rewrites, by-category and by-route breakdown.",
        testId: "admin-card-safety",
      },
    ],
  },
  {
    id: "analytics",
    kicker: "ANALYTICS",
    title: "Read behavior, not vanity.",
    description: "Read-only behavioral instrumentation. Persistence over engagement. The dashboards never trigger product changes — they inform them.",
    tools: [
      {
        to: "/admin/voice-metrics",
        name: "Voice Metrics",
        blurb: "Voice-messaging funnel, copy-rate by tone, D1 retention, edit-before-copy trust signal.",
        testId: "admin-card-voice-metrics",
      },
      {
        to: "/admin/anonymous-metrics",
        name: "Anonymous Metrics",
        blurb: "DAU/WAU, talkers vs lurkers, peak concurrent, block rate, D1/D7 retention, top rooms.",
        testId: "admin-card-anonymous-metrics",
      },
      {
        to: "/admin/debates/retention",
        name: "Debates Retention",
        blurb: "Five-ratio funnel, return-to-defend, D1/D7 cohorts, engagement quality, qualitative reads.",
        testId: "admin-card-debates-retention",
      },
      {
        to: "/admin/translation-chat",
        name: "Translation Metrics",
        blurb: "Avg messages per room, repeat-joiner gravity, language-corridor frequency, invite attribution.",
        testId: "admin-card-translation-metrics",
      },
      {
        to: "/admin/delayed-messages",
        name: "Delayed-Chat Persistence",
        blurb: "D7 / D30 voluntary-open rate, repeat composers, recipient-type breakdown. The gravity signal.",
        testId: "admin-card-delayed-metrics",
      },
      {
        to: "/admin/avatar-chat",
        name: "Avatar Render Queue",
        blurb: "Pipeline metrics + retry queue for the avatar-video presentation layer.",
        testId: "admin-card-avatar",
      },
      {
        to: "/admin/login-intelligence",
        name: "Login Intelligence",
        blurb: "Auth events, browser/OS breakdown, country distribution, failed-login concentration.",
        testId: "admin-card-login",
      },
    ],
  },
  {
    id: "operations",
    kicker: "OPERATIONS",
    title: "See what's happening across the platform.",
    description: "Cross-product surfaces. Use with restraint — these read raw user content under the disclosed-mode privacy notice.",
    tools: [
      {
        to: "/admin/chats",
        name: "Chats Monitor",
        blurb: "Unified read across clone / anonymous / debate / smart-reply. Redaction at read time. Flag / hide actions.",
        testId: "admin-card-chats",
      },
      {
        to: "/admin/webhook-logs",
        name: "Cashfree Webhooks",
        blurb: "Real-time log of every webhook arrival. Send signed (or tampered) test webhooks against the live endpoint.",
        testId: "admin-card-webhook-logs",
      },
      {
        to: "/admin/revenue",
        name: "Revenue Mirror",
        blurb: "Funnel · Revenue · Credit Economy · Emotional Gravity · Cohorts · Operational Health. Read-only. No interpretation.",
        testId: "admin-card-revenue",
      },
    ],
  },
];

export default function AdminIndex() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-6xl mx-auto px-4 sm:px-8 py-10 text-muted font-mono text-sm" data-testid="admin-index-loading">Loading…</div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login?redirect=/admin" replace />;
  }

  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16" data-testid="admin-index-forbidden">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <h1 className="font-display text-2xl font-bold text-ink mb-2">This area is for operators.</h1>
            <p className="text-sm text-muted">Your account does not have admin permissions. If this is a mistake, contact the platform owner.</p>
            <div className="mt-6">
              <Link to="/dashboard" className="btn-brutal text-sm" data-testid="admin-forbidden-back">Back to dashboard</Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen page-bg">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-10" data-testid="admin-index">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">Operator console</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Admin</h1>
          <p className="text-sm text-muted max-w-2xl">
            Every operator tool lives here. The public navbar is intentionally clean — admin surfaces are
            never visible to normal users. All routes below preserve their existing permissions.
          </p>
        </header>

        {SECTIONS.map((section) => (
          <section key={section.id} className="space-y-4" data-testid={`admin-section-${section.id}`}>
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <div>
                <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-muted">{section.kicker}</div>
                <h2 className="heading-display text-xl sm:text-2xl mt-0.5">{section.title}</h2>
              </div>
              <p className="text-xs text-muted max-w-md">{section.description}</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4">
              {section.tools.map((tool) => (
                <Link
                  key={tool.to}
                  to={tool.to}
                  className="brutal-card p-5 hover:translate-y-[-2px] transition-transform group block"
                  data-testid={tool.testId}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <h3 className="font-display font-bold text-base text-ink leading-snug">{tool.name}</h3>
                    <span className="text-violet-soft text-lg leading-none group-hover:translate-x-0.5 transition-transform" aria-hidden="true">→</span>
                  </div>
                  <p className="text-xs text-muted leading-relaxed">{tool.blurb}</p>
                </Link>
              ))}
            </div>
          </section>
        ))}

        <footer className="pt-6 border-t border-white/5 text-[11px] font-mono uppercase tracking-widest text-muted" data-testid="admin-index-footer">
          Read-only by default · Actions are audited · The system delivers; it does not chase.
        </footer>
      </div>
    </div>
  );
}
