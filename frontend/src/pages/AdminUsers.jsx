/**
 * Admin: Users → Subscription History
 *
 * Two-pane operator console for paying-customer support.
 *   - Left: search box that hits /api/admin/billing/users/search (email or
 *     user_id). Shows up to 25 matches.
 *   - Right: full subscription summary for the selected user:
 *       · derived lifecycle state (active / cancelled / refunded / etc.)
 *       · lifetime totals (revenue, credits purchased, credits consumed)
 *       · order history table
 *       · credit-event ledger
 *
 * Read-only. The only mutating action available here is "Adjust credits"
 * which lives in the existing /admin/billing/credit-adjust admin endpoint.
 */
import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); }
  catch { return iso; }
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function inrToRupees(amountInr) {
  if (amountInr == null) return "—";
  return `₹${Number(amountInr).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

const STATE_TONE = {
  active: "tag-emerald",
  pending_cancellation: "tag-amber",
  grace_period: "tag-amber",
  payment_failed: "tag-rose",
  expired: "tag-rose",
  cancelled: "tag-muted",
  refunded: "tag-amber",
  deleted: "tag-rose",
  free: "tag-muted",
  pending_verification: "tag-amber",
};

function StateTag({ state, label }) {
  const cls = STATE_TONE[state] || "tag-muted";
  return <span className={`tag ${cls}`} data-testid={`user-state-${state}`}>{label || state}</span>;
}

function MetricTile({ label, value, testId, tone }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className={`text-2xl font-display font-bold mt-0.5 ${tone || ""}`}>{value}</div>
    </div>
  );
}

export default function AdminUsers() {
  const { user, loading: authLoading } = useAuth();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState(null);
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [error, setError] = useState(null);

  const runSearch = async (q) => {
    if (!q || q.length < 1) { setResults([]); return; }
    setSearchLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/admin/billing/users/search?q=${encodeURIComponent(q)}`);
      setResults(data?.users || []);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Search failed.");
      setResults([]);
    } finally {
      setSearchLoading(false);
    }
  };

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { runSearch(query.trim()); }, 350);
    return () => clearTimeout(t);
  }, [query]);

  const selectUser = async (userId) => {
    setSelectedUserId(userId);
    setSummary(null);
    setSummaryLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/admin/billing/users/${userId}/subscription-summary`);
      setSummary(data);
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load user summary.");
    } finally {
      setSummaryLoading(false);
    }
  };

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/users" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="admin-users-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  const u = summary?.user;
  const s = summary?.state;
  const lt = summary?.lifetime;

  return (
    <div className="min-h-screen page-bg" data-testid="admin-users-page">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-6">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">OPERATIONS · USERS</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Users · subscription history.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Look up any user and see lifetime billing, derived subscription state, and the full order ledger.
            Read-only — use Anti-Abuse or the Cashfree Webhooks dashboards for mutations.
          </p>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
          {/* ── Search pane ─────────────────────────────────── */}
          <aside className="brutal-card p-4 space-y-3 lg:sticky lg:top-4 self-start" data-testid="admin-users-search-pane">
            <label className="text-[10px] font-mono uppercase tracking-widest text-muted block">Find user</label>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Email or user_id"
              className="input-brutal text-sm"
              data-testid="admin-users-search-input"
            />
            <div className="text-[10px] font-mono text-muted">
              {searchLoading ? "Searching…" : `${results.length} result${results.length === 1 ? "" : "s"}`}
            </div>
            <div className="max-h-[60vh] overflow-y-auto space-y-1.5" data-testid="admin-users-results">
              {results.map((r) => (
                <button
                  key={r.user_id}
                  type="button"
                  onClick={() => selectUser(r.user_id)}
                  className={`w-full text-left p-2.5 rounded-lg border transition text-xs ${
                    selectedUserId === r.user_id
                      ? "bg-violet-soft/15 border-violet-soft/40"
                      : "bg-white/[0.02] border-white/5 hover:bg-white/[0.06] hover:border-white/15"
                  }`}
                  data-testid={`admin-users-result-${r.user_id}`}
                >
                  <div className="font-medium text-ink truncate">{r.email}</div>
                  <div className="text-muted font-mono text-[10px] mt-0.5">{r.user_id}</div>
                  <div className="flex gap-1.5 mt-1.5 flex-wrap">
                    <span className="tag tag-muted">{r.plan_id || "free"}</span>
                    {r.is_deleted && <span className="tag tag-rose">deleted</span>}
                    {r.role === "admin" && <span className="tag tag-violet">admin</span>}
                    {r.abuse_status && r.abuse_status !== "normal" && (
                      <span className={`tag ${r.abuse_status === "blocked" ? "tag-rose" : "tag-amber"}`}>{r.abuse_status}</span>
                    )}
                  </div>
                </button>
              ))}
              {!searchLoading && results.length === 0 && query.length > 0 && (
                <div className="text-xs text-muted py-3" data-testid="admin-users-no-results">No matches.</div>
              )}
            </div>
            {error && (
              <div className="brutal-card p-2.5 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="admin-users-error">
                {error}
              </div>
            )}
          </aside>

          {/* ── Detail pane ─────────────────────────────────── */}
          <div className="space-y-6 min-w-0">
            {!selectedUserId && (
              <div className="brutal-card p-12 text-center" data-testid="admin-users-empty-state">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">SELECT A USER</div>
                <p className="text-sm text-muted">Search by email or user_id on the left to drill into their subscription history.</p>
              </div>
            )}

            {summaryLoading && (
              <div className="brutal-card p-8 text-muted font-mono text-sm" data-testid="admin-users-summary-loading">Loading user summary…</div>
            )}

            {summary && !summaryLoading && (
              <>
                {/* Profile + state */}
                <section className="brutal-card p-6" data-testid="admin-users-summary-card">
                  <div className="flex items-start justify-between flex-wrap gap-3">
                    <div>
                      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">User</div>
                      <h2 className="font-display text-2xl mt-0.5 break-all">{u?.email}</h2>
                      <div className="font-mono text-[11px] text-muted mt-1">{u?.user_id}</div>
                      <div className="flex gap-1.5 mt-2 flex-wrap">
                        <StateTag state={s?.state} label={s?.state_label} />
                        {s?.admin_unlimited && <span className="tag tag-violet">∞ Admin unlimited</span>}
                        {u?.is_deleted && <span className="tag tag-rose">Deleted</span>}
                        {u?.abuse_status && u.abuse_status !== "normal" && (
                          <span className={`tag ${u.abuse_status === "blocked" ? "tag-rose" : "tag-amber"}`}>{u.abuse_status}</span>
                        )}
                      </div>
                    </div>
                    <div className="text-right text-xs text-muted">
                      <div>Joined {formatDate(u?.created_at)}</div>
                      <div className="font-mono mt-1">{u?.auth_provider}</div>
                    </div>
                  </div>
                  {s?.state_reason && (
                    <div className="text-xs text-muted mt-3 pt-3 border-t border-white/5" data-testid="admin-users-state-reason">
                      {s.state_reason}
                    </div>
                  )}
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-4 pt-4 border-t border-white/5">
                    <div>
                      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Current plan</div>
                      <div className="text-sm mt-0.5">{s?.current_plan_name || "Free"}</div>
                    </div>
                    <div>
                      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Started</div>
                      <div className="text-sm mt-0.5">{formatDate(s?.started_at)}</div>
                    </div>
                    <div>
                      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">
                        {s?.state === "grace_period" ? "Grace until" : "Renews / Expires"}
                      </div>
                      <div className="text-sm mt-0.5">
                        {formatDate(s?.state === "grace_period" ? s?.grace_period_until : s?.expires_at)}
                      </div>
                    </div>
                  </div>
                </section>

                {/* Lifetime totals */}
                <section className="space-y-3" data-testid="admin-users-lifetime">
                  <h3 className="text-[11px] font-mono uppercase tracking-widest text-muted">Lifetime</h3>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                    <MetricTile
                      label="Revenue"
                      value={inrToRupees(lt?.total_revenue_inr)}
                      tone="text-emerald-300"
                      testId="lifetime-revenue"
                    />
                    <MetricTile
                      label="Paid orders"
                      value={lt?.total_paid_orders ?? 0}
                      testId="lifetime-paid-orders"
                    />
                    <MetricTile
                      label="Credits purchased"
                      value={Number(lt?.total_credits_purchased ?? 0).toLocaleString()}
                      testId="lifetime-credits-purchased"
                    />
                    <MetricTile
                      label="Credits consumed"
                      value={Number(lt?.total_credits_consumed ?? 0).toLocaleString()}
                      tone="text-amber"
                      testId="lifetime-credits-consumed"
                    />
                    <MetricTile
                      label="Current balance"
                      value={Number(lt?.current_credits_balance ?? 0).toLocaleString()}
                      testId="lifetime-current-balance"
                    />
                  </div>
                  {lt?.first_paid_at && (
                    <div className="text-[11px] font-mono text-muted">
                      First paid {formatDate(lt.first_paid_at)} · Last paid {formatDate(lt.last_paid_at)}
                    </div>
                  )}
                </section>

                {/* Order history */}
                <section className="space-y-3" data-testid="admin-users-orders">
                  <h3 className="text-[11px] font-mono uppercase tracking-widest text-muted">Order history</h3>
                  {(summary.orders || []).length === 0 ? (
                    <div className="brutal-card p-4 text-sm text-muted" data-testid="admin-users-orders-empty">No orders.</div>
                  ) : (
                    <div className="brutal-card overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                            <th className="p-3">Created</th>
                            <th className="p-3">Order</th>
                            <th className="p-3">Plan / Pack</th>
                            <th className="p-3">Amount</th>
                            <th className="p-3">Credits</th>
                            <th className="p-3">Status</th>
                            <th className="p-3">Paid at</th>
                          </tr>
                        </thead>
                        <tbody>
                          {summary.orders.map((o) => (
                            <tr key={o.order_id} className="border-t border-white/5" data-testid={`admin-user-order-${o.order_id}`}>
                              <td className="p-3 font-mono text-[11px] text-muted whitespace-nowrap">{formatDateTime(o.created_at)}</td>
                              <td className="p-3 font-mono text-[11px]">{o.order_id}</td>
                              <td className="p-3">{o.plan_id || o.pack_id || "—"}</td>
                              <td className="p-3 font-mono">{o.amount_inr != null ? inrToRupees(o.amount_inr) : "—"}</td>
                              <td className="p-3 font-mono">{o.credits_to_grant ?? "—"}</td>
                              <td className="p-3">
                                {o.refunded ? <span className="tag tag-amber">refunded</span> :
                                  o.status === "paid" ? <span className="tag tag-emerald">paid</span> :
                                  o.status === "failed" ? <span className="tag tag-rose">failed</span> :
                                  <span className="tag tag-muted">{o.status}</span>}
                              </td>
                              <td className="p-3 font-mono text-[11px] text-muted">{formatDateTime(o.paid_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>

                {/* Credit ledger (recent) */}
                <section className="space-y-3" data-testid="admin-users-credit-ledger">
                  <h3 className="text-[11px] font-mono uppercase tracking-widest text-muted">Credit ledger (last 200)</h3>
                  {(summary.credit_events || []).length === 0 ? (
                    <div className="brutal-card p-4 text-sm text-muted" data-testid="admin-users-credits-empty">No credit events.</div>
                  ) : (
                    <div className="brutal-card overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                            <th className="p-3">Time</th>
                            <th className="p-3">Kind</th>
                            <th className="p-3">Delta</th>
                            <th className="p-3">Balance after</th>
                            <th className="p-3">Surface</th>
                          </tr>
                        </thead>
                        <tbody>
                          {summary.credit_events.map((e, idx) => (
                            <tr key={e.event_id || idx} className="border-t border-white/5" data-testid={`admin-user-credit-event-${idx}`}>
                              <td className="p-3 font-mono text-muted whitespace-nowrap">{formatDateTime(e.created_at)}</td>
                              <td className="p-3 font-mono">{e.kind}</td>
                              <td className={`p-3 font-mono font-bold ${e.delta > 0 ? "text-emerald-300" : "text-rose-soft"}`}>
                                {e.delta > 0 ? `+${e.delta}` : e.delta}
                              </td>
                              <td className="p-3 font-mono">{e.balance_after}</td>
                              <td className="p-3 font-mono text-muted">{e.surface || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
