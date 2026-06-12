/**
 * Admin: User Activity
 *
 * One-stop per-user view that the three siloed admin surfaces never gave
 * us together:
 *   - WHO logged in (email, plan, role)
 *   - WHEN they last logged in (timestamp + city/country)
 *   - HOW active they are (login count + feature uses in the window)
 *   - WHICH features they touched (top 5 by deduct events)
 *   - Full chronological timeline on drill-down
 *
 * Read-only. Pure observation.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const WINDOW_OPTIONS = [7, 30, 90];
const PLAN_OPTIONS = ["", "free", "starter", "starter_chat", "pro", "premium", "ultimate_creator"];
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "last_login", label: "Last login" },
  { value: "logins", label: "Logins (desc)" },
  { value: "features", label: "Feature uses (desc)" },
  { value: "created", label: "Newest signup" },
  { value: "email", label: "Email A-Z" },
  { value: "plan", label: "Plan" },
];

const PLAN_TONE = {
  free: "border-white/15 text-muted bg-white/[0.03]",
  starter: "border-amber/40 text-amber bg-amber/10",
  starter_chat: "border-amber/40 text-amber bg-amber/10",
  pro: "border-violet/40 text-violet-soft bg-violet/10",
  premium: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10",
  ultimate_creator: "border-rose/40 text-rose-soft bg-rose/10",
};

function PlanBadge({ plan_id, plan_status }) {
  if (!plan_id) return <span className="text-muted text-[10px]">—</span>;
  const tone = PLAN_TONE[plan_id] || "border-white/15 text-muted bg-white/[0.03]";
  return (
    <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${tone}`}>
      {plan_id}{plan_status && plan_status !== "active" ? ` · ${plan_status}` : ""}
    </span>
  );
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function relative(iso) {
  if (!iso) return "—";
  try {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch { return "—"; }
}

function Location({ city, region, country }) {
  const parts = [city, region, country].filter(Boolean);
  if (parts.length === 0) return <span className="text-muted">—</span>;
  return <span>{parts.join(", ")}</span>;
}

function TimelineRow({ e }) {
  const tone = {
    login: "text-sky-300",
    feature_use: "text-emerald-300",
    paywall_hit: "text-rose-soft",
    subscription_transition: "text-violet-soft",
  }[e.kind] || "text-ink";
  return (
    <div className="grid grid-cols-[100px_110px_1fr] gap-2 py-1.5 border-t border-white/5 text-[12px]">
      <div className="font-mono text-[10px] text-muted">{formatDateTime(e.at)}</div>
      <div className={`font-mono text-[10px] uppercase tracking-widest ${tone}`}>
        {e.kind === "login" ? (e.success ? "login ✓" : "login ✗") : e.kind.replace(/_/g, " ")}
      </div>
      <div className="text-ink/90">
        {e.kind === "login" && (
          <>
            <Location city={e.city} region={e.region} country={e.country} />
            {e.method && <span className="text-muted"> · {e.method}</span>}
            {e.browser && <span className="text-muted"> · {e.browser} / {e.os}</span>}
            {e.failure_reason && <span className="text-rose-soft"> · {e.failure_reason}</span>}
          </>
        )}
        {e.kind === "feature_use" && (
          <>
            <span className="text-emerald-300">{e.feature}</span>
            <span className="text-muted"> · {e.surface}</span>
            <span className="text-muted"> · {e.credits} cr</span>
          </>
        )}
        {e.kind === "paywall_hit" && (
          <>
            <span className="text-rose-soft">{e.surface}</span>
            {e.reason && <span className="text-muted"> · {e.reason}</span>}
          </>
        )}
        {e.kind === "subscription_transition" && (
          <>
            <span className="text-violet-soft">{e.transition}</span>
            {e.from_plan && <span className="text-muted"> · {e.from_plan} → {e.to_plan}</span>}
          </>
        )}
      </div>
    </div>
  );
}

function UserDetailPanel({ userId, days, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.get(`/admin/user-activity/${userId}?days=${days}`)
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, [userId, days]);

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-stretch justify-end" onClick={onClose}
      data-testid="ua-detail-overlay">
      <div className="w-full max-w-3xl bg-[#0d0d10] border-l border-white/10 overflow-y-auto"
        onClick={(e) => e.stopPropagation()} data-testid="ua-detail-panel">
        <div className="p-5 border-b border-white/10 flex items-center justify-between">
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted">User activity · {days}d</div>
            <div className="text-base font-display font-bold mt-0.5" data-testid="ua-detail-email">
              {data?.user?.email || (loading ? "Loading…" : "?")}
            </div>
          </div>
          <button className="btn-ghost text-xs" onClick={onClose} data-testid="ua-detail-close">Close</button>
        </div>

        {loading || !data ? (
          <div className="p-8 text-sm text-muted">Loading…</div>
        ) : (
          <div className="p-5 space-y-5">
            <section className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid="ua-detail-tiles">
              <div className="brutal-card p-3">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Plan</div>
                <div className="mt-1"><PlanBadge plan_id={data.user.plan_id} plan_status={data.user.plan_status} /></div>
              </div>
              <div className="brutal-card p-3">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Credits</div>
                <div className="text-lg font-display font-semibold mt-0.5">{data.user.credits_balance ?? 0}</div>
              </div>
              <div className="brutal-card p-3">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Logins · {days}d</div>
                <div className="text-lg font-display font-semibold mt-0.5">{data.summary.logins_in_window}</div>
              </div>
              <div className="brutal-card p-3">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Feature uses</div>
                <div className="text-lg font-display font-semibold mt-0.5">{data.summary.feature_uses_in_window}</div>
              </div>
            </section>

            <section className="brutal-card p-4 text-[12px] space-y-1.5" data-testid="ua-detail-meta">
              <div><span className="text-muted">user_id</span> · <code className="text-amber/90">{data.user.user_id}</code></div>
              <div><span className="text-muted">auth</span> · {data.user.auth_provider || "email"} {data.user.email_verified ? "· verified" : "· unverified"}</div>
              <div><span className="text-muted">created</span> · {formatDateTime(data.user.created_at)}</div>
              <div>
                <span className="text-muted">last login</span> · {formatDateTime(data.summary.last_login_at)}
                {(data.summary.last_login_city || data.summary.last_login_country) && (
                  <span className="text-muted"> · <Location
                    city={data.summary.last_login_city}
                    country={data.summary.last_login_country} /></span>
                )}
                {data.summary.last_login_method && <span className="text-muted"> · {data.summary.last_login_method}</span>}
              </div>
              {data.user.cancel_at_period_end && (
                <div className="text-amber">
                  Pending cancellation · requested {formatDateTime(data.user.cancel_requested_at)}
                  {data.user.cancel_reason && ` · "${data.user.cancel_reason}"`}
                </div>
              )}
            </section>

            {data.summary.top_features?.length > 0 && (
              <section data-testid="ua-detail-top-features">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">Top features · {days}d</div>
                <div className="flex flex-wrap gap-2">
                  {data.summary.top_features.map((f) => (
                    <span key={f.feature} className="px-2.5 py-1 rounded-full border border-emerald-500/30 bg-emerald-500/5 text-[11px] font-mono text-emerald-300">
                      {f.feature} · {f.count}
                    </span>
                  ))}
                </div>
              </section>
            )}

            <section data-testid="ua-detail-timeline">
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">
                Timeline · {data.timeline.length} events
              </div>
              {data.timeline.length === 0 ? (
                <div className="brutal-card p-6 text-sm text-muted">No activity in window.</div>
              ) : (
                <div className="brutal-card p-3 max-h-[60vh] overflow-y-auto">
                  {data.timeline.map((e, i) => <TimelineRow key={`${e.kind}-${e.at}-${i}`} e={e} />)}
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AdminUserActivity() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(30);
  const [q, setQ] = useState("");
  const [planFilter, setPlanFilter] = useState("");
  const [sort, setSort] = useState("last_active");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedUserId, setSelectedUserId] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        days: String(days), sort, page: String(page), limit: "25",
      });
      if (q.trim()) params.set("q", q.trim());
      if (planFilter) params.set("plan", planFilter);
      const r = await api.get(`/admin/user-activity?${params.toString()}`);
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load user activity.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!authLoading && user?.role === "admin") {
      Promise.resolve().then(load);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user, days, planFilter, sort, page]);

  // Debounced search
  useEffect(() => {
    if (!user || user.role !== "admin") return;
    const t = setTimeout(() => { setPage(1); load(); }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/user-activity" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="ua-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  const items = data?.items || [];
  const totalPages = data ? Math.ceil((data.total_candidates || 0) / 25) : 1;

  return (
    <div className="min-h-screen page-bg" data-testid="admin-user-activity-page">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-6">
        <header className="space-y-1.5">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">ANALYTICS · USER ACTIVITY</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Who is using the app, where, and how.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Per-user view: last login (timestamp + city/country), login count,
            feature uses, top features, and full chronological timeline on
            drill-down. Read-only.
          </p>
        </header>

        <section className="brutal-card p-3 flex flex-wrap items-center gap-3" data-testid="ua-controls">
          <input
            type="text"
            placeholder="Search by email or user_id…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="input-brutal text-sm flex-1 min-w-[200px]"
            data-testid="ua-search"
          />
          <select
            value={planFilter}
            onChange={(e) => { setPlanFilter(e.target.value); setPage(1); }}
            className="input-brutal text-sm py-1.5"
            data-testid="ua-plan-filter"
          >
            <option value="">All plans</option>
            {PLAN_OPTIONS.filter(Boolean).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select
            value={sort}
            onChange={(e) => { setSort(e.target.value); setPage(1); }}
            className="input-brutal text-sm py-1.5"
            data-testid="ua-sort"
          >
            {SORT_OPTIONS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-muted">
            <span>Window:</span>
            {WINDOW_OPTIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => { setDays(d); setPage(1); }}
                className={`px-2.5 py-1 rounded-md border text-xs font-mono ${
                  days === d ? "bg-amber/20 border-amber/50 text-amber" : "bg-white/[0.02] border-white/10 text-ink/70 hover:bg-white/[0.06]"
                }`}
                data-testid={`ua-window-${d}`}
              >
                {d}d
              </button>
            ))}
          </div>
        </section>

        {error && (
          <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="ua-error">{error}</div>
        )}

        <section className="brutal-card overflow-x-auto" data-testid="ua-table-section">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                <th className="p-3">User</th>
                <th className="p-3">Plan</th>
                <th className="p-3">Last login</th>
                <th className="p-3">Location</th>
                <th className="p-3 text-right">Logins · {days}d</th>
                <th className="p-3 text-right">Features · {days}d</th>
                <th className="p-3">Top features</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={7} className="p-6 text-center text-muted text-sm">Loading…</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={7} className="p-6 text-center text-muted text-sm" data-testid="ua-empty">No users match these filters.</td></tr>
              )}
              {!loading && items.map((u) => (
                <tr
                  key={u.user_id}
                  className="border-t border-white/5 hover:bg-white/[0.03] cursor-pointer transition"
                  onClick={() => setSelectedUserId(u.user_id)}
                  data-testid={`ua-row-${u.user_id}`}
                >
                  <td className="p-3">
                    <div className="text-[13px] truncate max-w-[260px]">{u.email || u.user_id}</div>
                    <div className="text-[10px] font-mono text-muted">
                      {u.role === "admin" && <span className="text-violet-soft mr-1.5">admin</span>}
                      {u.user_id}
                    </div>
                  </td>
                  <td className="p-3"><PlanBadge plan_id={u.plan_id} plan_status={u.plan_status} /></td>
                  <td className="p-3 font-mono text-[11px]">
                    <div>{relative(u.last_login_at)}</div>
                    <div className="text-muted">{formatDateTime(u.last_login_at)}</div>
                  </td>
                  <td className="p-3 text-[12px]">
                    <Location city={u.last_login_city} region={u.last_login_region} country={u.last_login_country} />
                    {u.last_login_method && <div className="text-[10px] font-mono text-muted">{u.last_login_method}</div>}
                  </td>
                  <td className="p-3 text-right font-mono text-[13px]">{u.logins_in_window}</td>
                  <td className="p-3 text-right font-mono text-[13px]">{u.feature_uses_in_window}</td>
                  <td className="p-3">
                    {u.top_features?.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {u.top_features.slice(0, 3).map((f) => (
                          <span key={f.feature} className="px-1.5 py-0.5 rounded-full border border-emerald-500/30 bg-emerald-500/5 text-[9px] font-mono text-emerald-300">
                            {f.feature}·{f.count}
                          </span>
                        ))}
                      </div>
                    ) : <span className="text-muted text-[10px]">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {data && totalPages > 1 && (
          <div className="flex items-center justify-between" data-testid="ua-pagination">
            <span className="text-[11px] font-mono text-muted">
              Page {page} of {totalPages} · {data.total_candidates} users
            </span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="btn-ghost text-xs disabled:opacity-30"
                data-testid="ua-page-prev"
              >
                ← Prev
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="btn-ghost text-xs disabled:opacity-30"
                data-testid="ua-page-next"
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>

      {selectedUserId && (
        <UserDetailPanel
          userId={selectedUserId}
          days={days}
          onClose={() => setSelectedUserId(null)}
        />
      )}
    </div>
  );
}
