/**
 * Debates Retention — read-only behavioral observability.
 *
 * Operator constraint: this page is INSTRUMENTATION, not product expansion.
 * It does NOT prompt users, notify, or change product behavior.
 * Its job is to test the operator's three identity hypotheses:
 *   - Intellectual: long arguments, high session depth, low velocity
 *   - Tribal: high return-to-defend, high vote velocity, fast tempo
 *   - Performative: high share rate, high score-optimization, screenshot signal
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function pct(n) {
  if (n === null || n === undefined) return "—";
  return `${Number(n).toFixed(1)}%`;
}
function num(n) {
  if (n === null || n === undefined) return "—";
  if (typeof n !== "number") return String(n);
  if (n >= 1000) return n.toLocaleString();
  return String(n);
}

function StatCard({ label, value, sub, testid, tone = "default" }) {
  const toneClass = tone === "good" ? "border-emerald" : tone === "bad" ? "border-rose" : tone === "warn" ? "border-amber" : "";
  return (
    <div className={`brutal-card p-4 sm:p-5 ${toneClass}`} data-testid={testid}>
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display font-black text-2xl sm:text-3xl mt-1 text-ink break-words">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

function FunnelBar({ label, value, base, testid }) {
  const pctVal = base > 0 ? Math.min(100, Math.round((value / base) * 100)) : 0;
  return (
    <div data-testid={testid}>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</span>
        <span className="text-xs font-mono text-ink">{num(value)} <span className="text-muted">({base > 0 ? `${pctVal}%` : "—"})</span></span>
      </div>
      <div className="h-2 bg-ink/5 rounded-full overflow-hidden">
        <div className="h-full bg-gradient-to-r from-amber to-violet" style={{ width: `${pctVal}%` }} />
      </div>
    </div>
  );
}

function SectionTitle({ children, testid }) {
  return (
    <div className="flex items-baseline justify-between mt-8 mb-3">
      <h2 className="text-base sm:text-lg font-mono uppercase tracking-widest text-ink/85" data-testid={testid}>{children}</h2>
    </div>
  );
}

export default function AdminDebatesRetention() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [err, setErr] = useState("");
  const [exporting, setExporting] = useState(false);

  const fetchOnce = useCallback(async () => {
    try {
      const r = await api.get(`/admin/debates/retention?days=${days}`);
      setData(r.data);
      setForbidden(false);
      setErr("");
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
      else setErr(e?.response?.data?.detail || "Could not load retention.");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/debates/retention"); return; }
    if (!user) return;
    setLoading(true);
    fetchOnce();
  }, [user, authLoading, days, navigate, fetchOnce]);

  const exportEvents = async () => {
    setExporting(true);
    try {
      const r = await api.get(`/admin/debates/events/export?days=${days}&limit=100000`);
      const blob = new Blob([JSON.stringify(r.data.events, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `debate_events_${days}d_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  if (forbidden) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 py-10">
          <div className="brutal-card p-8 text-center" data-testid="debates-retention-forbidden">
            <h1 className="heading-display text-2xl mb-2">Admin only</h1>
          </div>
        </div>
      </div>
    );
  }

  const f = data?.funnel;
  const r2d = data?.return_to_defend;
  const e = data?.engagement;
  const ret = data?.retention;
  const cohorts = data?.cohorts_first_category || [];
  const fastest = data?.qualitative?.fastest_rising || [];
  const reported = data?.qualitative?.most_reported || [];

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="debates-retention-page">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-2">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">AI Debate Rooms · Retention</div>
            <h1 className="heading-display text-3xl sm:text-4xl mt-1">Behavioral evidence dashboard</h1>
            <p className="text-sm text-muted mt-2 max-w-2xl">
              Read-only instrumentation. No notifications. No behavior-shaping infra. Use this to test which of intellectual / tribal / performative the product is actually becoming.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 7, 14, 30].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                data-testid={`debates-retention-window-${d}d`}
                className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border transition-colors ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`}
              >
                {d === 1 ? "24h" : `${d}d`}
              </button>
            ))}
            <button onClick={exportEvents} disabled={exporting} className="btn-ghost text-xs disabled:opacity-50" data-testid="debates-retention-export-btn">
              {exporting ? "Exporting…" : "Export raw events"}
            </button>
            <Link to="/admin/debates" className="text-xs font-mono uppercase tracking-widest text-ink/70 hover:text-ink underline underline-offset-4 ml-2" data-testid="debates-retention-mod-link">
              Moderation →
            </Link>
          </div>
        </div>

        {err && <div className="brutal-card p-4 text-sm text-rose-300 mb-6">{err}</div>}
        {loading && !data && <div className="text-muted font-mono text-sm">loading…</div>}

        {data && (
          <>
            {/* RETURN-TO-DEFEND — the gold signal */}
            <SectionTitle testid="debates-retention-section-r2d">Return-to-defend (gold signal)</SectionTitle>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 sm:gap-4">
              <StatCard
                testid="debates-retention-r2d-pct"
                label="Return-to-defend"
                value={r2d?.pct === null || r2d?.pct === undefined ? "—" : pct(r2d?.pct)}
                sub={`${num(r2d?.returned)}/${num(r2d?.submitter_debate_pairs)} submitter-debate pairs`}
                tone={r2d?.pct >= 30 ? "good" : r2d?.pct >= 10 ? "warn" : (r2d?.pct === null || r2d?.pct === undefined ? "default" : "bad")}
              />
              <StatCard
                testid="debates-retention-d1"
                label="D1 retention"
                value={ret?.d1?.pct === null || ret?.d1?.pct === undefined ? "—" : pct(ret?.d1?.pct)}
                sub={`${num(ret?.d1?.returned)}/${num(ret?.d1?.eligible)} eligible`}
              />
              <StatCard
                testid="debates-retention-d7"
                label="D7 retention"
                value={ret?.d7?.pct === null || ret?.d7?.pct === undefined ? "—" : pct(ret?.d7?.pct)}
                sub={`${num(ret?.d7?.returned)}/${num(ret?.d7?.eligible)} eligible`}
              />
            </div>
            <div className="text-[11px] font-mono text-muted mt-3" data-testid="debates-retention-r2d-note">
              Definition: a user who submitted at least one argument AND emitted any subsequent event on the same debate ≥ 30 minutes later. No notifications exist — every return is unprompted.
            </div>

            {/* FUNNEL — the 5 ratios */}
            <SectionTitle testid="debates-retention-section-funnel">Funnel · the five ratios (distinct users)</SectionTitle>
            <div className="brutal-card p-5 space-y-4" data-testid="debates-retention-funnel">
              <FunnelBar testid="funnel-list-viewed" label="List viewed" value={f?.list_viewed_users || 0} base={f?.list_viewed_users || 0} />
              <FunnelBar testid="funnel-opened" label="Debate opened" value={f?.opened_users || 0} base={f?.list_viewed_users || 0} />
              <FunnelBar testid="funnel-joined" label="Joined a side" value={f?.joined_users || 0} base={f?.opened_users || 0} />
              <FunnelBar testid="funnel-submitted" label="Submitted argument" value={f?.submitted_users || 0} base={f?.joined_users || 0} />
              <FunnelBar testid="funnel-voted" label="Voted" value={f?.voted_users || 0} base={f?.opened_users || 0} />
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
                <div className="text-xs font-mono text-muted" data-testid="funnel-ratio-open">open rate · <span className="text-ink">{pct(f?.open_rate_pct)}</span></div>
                <div className="text-xs font-mono text-muted" data-testid="funnel-ratio-join">join rate · <span className="text-ink">{pct(f?.join_rate_pct)}</span></div>
                <div className="text-xs font-mono text-muted" data-testid="funnel-ratio-arg">argument rate · <span className="text-ink">{pct(f?.argument_rate_pct)}</span></div>
                <div className="text-xs font-mono text-muted" data-testid="funnel-ratio-vote">vote rate · <span className="text-ink">{pct(f?.vote_rate_pct)}</span></div>
              </div>
            </div>

            {/* ENGAGEMENT QUALITY */}
            <SectionTitle testid="debates-retention-section-engagement">Engagement quality</SectionTitle>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <StatCard testid="engagement-submitters" label="Submitters" value={num(e?.submitters)} sub={`${pct(e?.multi_submitter_pct)} submit >1`} />
              <StatCard testid="engagement-avg-args" label="Avg args / submitter" value={num(e?.avg_args_per_submitter)} sub="repeat tendency" />
              <StatCard testid="engagement-avg-len" label="Avg argument length" value={`${num(e?.avg_argument_length_chars)} chars`} sub="long = intellectual · short = tribal" />
              <StatCard testid="engagement-lurker" label="Lurker rate" value={pct(e?.lurker_pct)} sub={`${num(e?.lurkers)} of ${num(e?.openers)} openers`} />
            </div>

            {/* COHORTS — first-debate-category */}
            <SectionTitle testid="debates-retention-section-cohorts">First-debate cohort · do certain categories retain better?</SectionTitle>
            <div className="brutal-card overflow-x-auto" data-testid="debates-retention-cohorts">
              <table className="w-full text-sm">
                <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                  <tr className="border-b border-ink/10">
                    <th className="text-left p-3">Category</th>
                    <th className="text-right p-3">Users</th>
                    <th className="text-right p-3">Submit %</th>
                    <th className="text-right p-3">Vote %</th>
                    <th className="text-right p-3">D1+ return %</th>
                  </tr>
                </thead>
                <tbody>
                  {cohorts.length === 0 && (
                    <tr><td colSpan="5" className="p-6 text-center text-muted">No cohort data yet.</td></tr>
                  )}
                  {cohorts.map((c) => (
                    <tr key={c.category} className="border-b border-ink/5" data-testid={`cohort-row-${c.category}`}>
                      <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">{c.category}</td>
                      <td className="p-3 text-right tabular-nums">{c.users}</td>
                      <td className="p-3 text-right tabular-nums">{pct(c.submit_rate_pct)}</td>
                      <td className="p-3 text-right tabular-nums">{pct(c.vote_rate_pct)}</td>
                      <td className="p-3 text-right tabular-nums">{pct(c.d1_return_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="text-[11px] font-mono text-muted mt-3">
              Watch for: which category becomes a repeat-engagement engine, not which is most "interesting."
            </div>

            {/* QUALITATIVE OBSERVATION */}
            <SectionTitle testid="debates-retention-section-qual">Qualitative · read these manually</SectionTitle>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div data-testid="debates-retention-fastest">
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Fastest-rising arguments (24h)</div>
                <div className="space-y-2">
                  {fastest.length === 0 && <div className="brutal-card p-4 text-xs font-mono text-muted text-center">No data yet.</div>}
                  {fastest.map((a) => (
                    <div key={a.argument_id} className="brutal-card p-3">
                      <div className="flex items-center justify-between text-[10px] font-mono text-muted">
                        <span>{a.anonymous_handle} · side {a.side}</span>
                        <span>AI {a.ai_score} · ▲{a.upvotes} ▼{a.downvotes} · rank {a.rank_score}</span>
                      </div>
                      <div className="text-sm text-ink/85 mt-1 line-clamp-2">{a.content}</div>
                    </div>
                  ))}
                </div>
              </div>
              <div data-testid="debates-retention-reported">
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Most-reported arguments</div>
                <div className="space-y-2">
                  {reported.length === 0 && <div className="brutal-card p-4 text-xs font-mono text-muted text-center">No reports yet.</div>}
                  {reported.map((a) => (
                    <div key={a.argument_id} className="brutal-card p-3">
                      <div className="flex items-center justify-between text-[10px] font-mono text-muted">
                        <span>{a.anonymous_handle} · side {a.side}</span>
                        <span>AI {a.ai_score} · {a.report_count} reports</span>
                      </div>
                      <div className="text-sm text-ink/85 mt-1 line-clamp-2 italic">"{a.content}"</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="text-[11px] font-mono text-muted mt-8" data-testid="debates-retention-footer">
              generated · {new Date(data.generated_at).toLocaleString()} · window {data.window_days}d · read-only · no behavior-shaping
            </div>
          </>
        )}
      </div>
    </div>
  );
}
