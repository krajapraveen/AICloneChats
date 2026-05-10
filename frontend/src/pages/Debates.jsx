/**
 * Debates list page — public.
 *
 * Shows seeded + admin-created debates. Click a card → /debates/:slug.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";

function pct(a, b) {
  const t = a + b;
  if (!t) return 50;
  return Math.round((a / t) * 100);
}

function DebateCard({ d }) {
  return (
    <Link
      to={`/debates/${d.slug}`}
      className="brutal-card p-5 sm:p-6 flex flex-col group hover:translate-y-[-2px] transition-transform"
      data-testid={`debate-card-${d.slug}`}
    >
      <div className="flex items-center gap-2 mb-2">
        {d.is_featured && <span className="tag tag-amber text-[10px]" data-testid={`debate-card-featured-${d.slug}`}>FEATURED</span>}
        <span className="tag tag-sky text-[10px] uppercase">{d.category}</span>
        {d.status !== "active" && <span className="tag tag-rose text-[10px]">{d.status}</span>}
      </div>
      <h3 className="font-display font-bold text-lg sm:text-xl text-ink leading-snug mb-2 group-hover:text-amber transition-colors" data-testid={`debate-card-title-${d.slug}`}>
        {d.title}
      </h3>
      <p className="text-sm text-ink/70 leading-relaxed line-clamp-2 mb-4">{d.description}</p>
      <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest mb-4">
        <span className="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-soft">{d.side_a_label}</span>
        <span className="text-muted">vs</span>
        <span className="px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-300">{d.side_b_label}</span>
      </div>
      <div className="mt-auto flex items-center justify-between text-xs font-mono text-muted">
        <span data-testid={`debate-card-stats-${d.slug}`}>
          {d.participant_count} debaters · {d.argument_count} args · {d.vote_count} votes
        </span>
        <span className="text-amber group-hover:translate-x-0.5 transition-transform">enter →</span>
      </div>
    </Link>
  );
}

export default function Debates() {
  const [debates, setDebates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.post("/analytics/event", { event_name: "debate_list_viewed", metadata: { experience_variant: "debate_v1" } }).catch(() => {});
    api.get("/debates")
      .then((r) => setDebates(r.data?.debates || []))
      .catch((e) => setErr(e?.response?.data?.detail || "Could not load debates."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="debates-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="mb-8">
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted">AI Debate Rooms</div>
          <h1 className="heading-display text-3xl sm:text-5xl mt-1">Pick a side. Make your case.</h1>
          <p className="text-sm sm:text-base text-muted mt-3 max-w-2xl leading-relaxed">
            AI scores your argument on clarity, logic, evidence, and civility. The crowd votes. The strongest reasoning rises.
          </p>
        </div>

        {err && <div className="brutal-card p-4 text-sm text-rose-300 mb-6" data-testid="debates-error">{err}</div>}
        {loading && <div className="text-muted font-mono text-sm" data-testid="debates-loading">loading debates…</div>}

        {!loading && debates.length === 0 && !err && (
          <div className="brutal-card p-8 text-center">
            <div className="text-muted font-mono text-sm">No debates yet. Check back soon.</div>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-5" data-testid="debates-grid">
          {debates.map((d) => (
            <DebateCard key={d.slug} d={d} />
          ))}
        </div>
      </div>
    </div>
  );
}
