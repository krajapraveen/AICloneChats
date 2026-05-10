/**
 * Admin Safety Dashboard — read-only.
 * Surfaces blocked unsafe input/output counts, by category, by route, recent events.
 * Stores ONLY hashes + 60-char snippets. Never raw unsafe text.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function num(n) { return n === null || n === undefined ? "—" : (typeof n === "number" && n >= 1000 ? n.toLocaleString() : String(n)); }

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

export default function AdminSafety() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);

  const fetchOnce = useCallback(async () => {
    try {
      const r = await api.get(`/admin/safety/moderation?days=${days}&limit=100`);
      setData(r.data);
      setForbidden(false);
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/safety"); return; }
    if (!user) return;
    setLoading(true);
    fetchOnce();
  }, [user, authLoading, days, navigate, fetchOnce]);

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;
  if (forbidden) return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="max-w-3xl mx-auto px-4 py-10">
        <div className="brutal-card p-8 text-center" data-testid="admin-safety-forbidden">
          <h1 className="heading-display text-2xl mb-2">Admin only</h1>
        </div>
      </div>
    </div>
  );

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="admin-safety-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-2">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Safety · Moderation</div>
            <h1 className="heading-display text-3xl sm:text-4xl mt-1">Safety filter activity</h1>
            <p className="text-sm text-muted mt-2 max-w-2xl">
              Shows blocked unsafe inputs and rewritten AI outputs across all surfaces.
              Stores ONLY content hashes + 60-char snippets — never raw unsafe text.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 7, 14, 30].map((d) => (
              <button key={d} onClick={() => setDays(d)} data-testid={`admin-safety-window-${d}d`}
                className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`}>
                {d === 1 ? "24h" : `${d}d`}
              </button>
            ))}
          </div>
        </div>

        {loading && !data && <div className="text-muted font-mono text-sm">loading…</div>}
        {data && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mt-6">
              <StatCard testid="safety-blocked-total" label="Blocked total" value={num(data.blocked_total)} sub="input + output blocks" tone={data.blocked_total > 100 ? "warn" : "default"} />
              <StatCard testid="safety-rewrite-total" label="Rewrites" value={num(data.rewrite_total)} sub="output sanitized" />
              <StatCard testid="safety-categories-count" label="Categories hit" value={num((data.by_category || []).length)} sub="distinct types" />
              <StatCard testid="safety-routes-count" label="Routes affected" value={num((data.by_route || []).length)} sub="surfaces flagged" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
              <div className="brutal-card overflow-x-auto" data-testid="safety-by-category">
                <div className="px-4 pt-3 text-[11px] font-mono uppercase tracking-widest text-muted">By category</div>
                <table className="w-full text-sm">
                  <tbody>
                    {(data.by_category || []).length === 0 && <tr><td className="p-4 text-center text-muted text-xs">No flagged events.</td></tr>}
                    {(data.by_category || []).map((c) => (
                      <tr key={c.category} className="border-b border-ink/5">
                        <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">{c.category}</td>
                        <td className="p-3 text-right tabular-nums">{c.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="brutal-card overflow-x-auto" data-testid="safety-by-route">
                <div className="px-4 pt-3 text-[11px] font-mono uppercase tracking-widest text-muted">By route</div>
                <table className="w-full text-sm">
                  <tbody>
                    {(data.by_route || []).length === 0 && <tr><td className="p-4 text-center text-muted text-xs">No flagged events.</td></tr>}
                    {(data.by_route || []).map((r) => (
                      <tr key={r.route} className="border-b border-ink/5">
                        <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">{r.route}</td>
                        <td className="p-3 text-right tabular-nums">{r.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="mt-6">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Recent flagged events</div>
              <div className="brutal-card overflow-x-auto" data-testid="safety-recent">
                <table className="w-full text-sm">
                  <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                    <tr className="border-b border-ink/10">
                      <th className="text-left p-3">When</th>
                      <th className="text-left p-3">Route · source</th>
                      <th className="text-left p-3">Category</th>
                      <th className="text-left p-3">Severity</th>
                      <th className="text-left p-3">Action</th>
                      <th className="text-left p-3">Snippet</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.recent || []).length === 0 && <tr><td colSpan="6" className="p-6 text-center text-xs text-muted">No flagged events.</td></tr>}
                    {(data.recent || []).map((ev) => (
                      <tr key={ev.event_id} className="border-b border-ink/5">
                        <td className="p-3 text-[11px] font-mono text-muted">{new Date(ev.created_at).toLocaleString()}</td>
                        <td className="p-3 font-mono text-xs">{ev.route} · {ev.source}</td>
                        <td className="p-3 font-mono text-xs">{ev.category || "—"}</td>
                        <td className="p-3 font-mono text-xs">{ev.severity || "—"}</td>
                        <td className="p-3 font-mono text-xs">{ev.action_taken}</td>
                        <td className="p-3 text-xs italic text-ink/70 max-w-[300px] truncate" title={ev.snippet}>"{ev.snippet}"</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="mt-8 text-[10px] font-mono text-muted">
              <Link className="hover:text-ink underline" to="/admin/debates/retention">Debates retention</Link>
              {" · "}
              <Link className="hover:text-ink underline" to="/admin/anonymous-metrics">Anonymous metrics</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
