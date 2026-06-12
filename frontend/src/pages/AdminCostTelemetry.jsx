/**
 * Admin: Cost Telemetry
 *
 * Two sections, one screen:
 *   1. Profit per feature — credits consumed, estimated cost, apportioned
 *      revenue, gross profit, margin %.
 *   2. Feature contribution to revenue — funnel by `pricing_visit_source`.
 *
 * Costs are operator-configurable via an inline editor. We never guess —
 * features without a configured cost-per-credit are rendered as "—" and
 * excluded from margin math. Legacy rows (no feature / no source) bucket
 * to `unknown` via $ifNull on the backend.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const WINDOW_OPTIONS = [7, 30, 90];

const FEATURE_TONE = {
  ai_clone: "text-violet-soft",
  voice: "text-amber",
  video: "text-rose-soft",
  chat: "text-emerald-300",
  image: "text-sky-300",
  avatar: "text-amber",
  unknown: "text-muted",
};

function inr(v) {
  if (v == null) return "—";
  return `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function pct(v) {
  if (v == null) return "—";
  return `${v}%`;
}

function Tile({ label, value, tone, testId, sub }) {
  return (
    <div className="brutal-card p-4" data-testid={testId}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className={`text-2xl font-display font-bold mt-0.5 ${tone || ""}`}>{value ?? "—"}</div>
      {sub && <div className="text-[11px] font-mono text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

const SEVERITY_TAG = {
  critical: "tag-rose",
  warning: "tag-amber",
  info: "tag-sky",
  ok: "tag-emerald",
  unknown: "tag-muted",
};

function formatDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function RequestTable({ rows, testIdPrefix }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
          <th className="p-3">When</th>
          <th className="p-3">User</th>
          <th className="p-3">Feature</th>
          <th className="p-3">Provider · model</th>
          <th className="p-3 text-right">Credits</th>
          <th className="p-3 text-right">Cost</th>
          <th className="p-3 text-right">Revenue</th>
          <th className="p-3 text-right">Margin</th>
          <th className="p-3">Severity</th>
          <th className="p-3">Recovery action</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const a = r.recovery_action || {};
          const kindTone = a.kind === "data_validation" ? "text-amber" :
            a.kind === "healthy" ? "text-emerald-300" :
            a.kind === "abuse_review" ? "text-rose-soft" :
            a.kind === "model_downgrade" ? "text-violet-soft" :
            a.kind === "raise_credit_cost" ? "text-amber" : "text-muted";
          return (
            <tr key={r.cost_id} className="border-t border-white/5" data-testid={`${testIdPrefix}-${r.cost_id}`}>
              <td className="p-3 font-mono text-[11px] text-muted whitespace-nowrap">{formatDateTime(r.created_at)}</td>
              <td className="p-3 font-mono text-[11px]">{r.user_id || "—"}</td>
              <td className={`p-3 font-mono ${FEATURE_TONE[r.feature] || ""}`}>{r.feature}</td>
              <td className="p-3 font-mono text-[11px] text-muted">{r.provider}<br/>{r.model}</td>
              <td className="p-3 text-right font-mono">{r.credits_deducted}</td>
              <td className="p-3 text-right font-mono text-rose-soft">{inr(r.metered_cost_inr)}</td>
              <td className="p-3 text-right font-mono">{inr(r.estimated_revenue_inr)}</td>
              <td className={`p-3 text-right font-mono font-bold ${(r.margin_inr ?? 0) >= 0 ? "text-emerald-300" : "text-rose-soft"}`}>
                {inr(r.margin_inr)}
                {r.margin_pct != null && (<div className="text-[10px] text-muted">{r.margin_pct}%</div>)}
              </td>
              <td className="p-3"><span className={`tag ${SEVERITY_TAG[r.severity] || "tag-muted"}`}>{r.severity}</span></td>
              <td className="p-3 max-w-[260px]" data-testid={`${testIdPrefix}-${r.cost_id}-recovery`}>
                <div className={`font-mono text-[11px] font-bold ${kindTone}`}>{a.label || "—"}</div>
                {a.reason && <div className="text-[10px] text-muted mt-0.5 line-clamp-3" title={a.reason}>{a.reason}</div>}
                {a.estimated_margin_gain_inr != null && a.estimated_margin_gain_inr > 0 && (
                  <div className="text-[10px] text-emerald-300 mt-0.5">Est. gain: +{inr(a.estimated_margin_gain_inr)}</div>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function FeatureCostEditor({ values, onSave, saving }) {
  const [draft, setDraft] = useState(() => Object.fromEntries(
    ["ai_clone", "voice", "video", "chat", "image", "avatar", "unknown"].map((f) => [f, values[f] ?? ""])
  ));
  const update = (k, v) => setDraft((p) => ({ ...p, [k]: v }));

  const submit = () => {
    const cleaned = {};
    for (const [k, v] of Object.entries(draft)) {
      if (v === "" || v == null) continue;
      const n = Number(v);
      if (!Number.isFinite(n) || n < 0) {
        toast.error(`Invalid cost for ${k} (must be ≥ 0)`);
        return;
      }
      cleaned[k] = n;
    }
    onSave(cleaned);
  };

  return (
    <div className="brutal-card p-4 space-y-3" data-testid="cost-editor">
      <div className="text-[10px] font-mono uppercase tracking-widest text-amber">Configure cost per credit</div>
      <p className="text-xs text-muted">
        Estimated INR cost per credit consumed for each feature. Leave blank if you
        haven&apos;t measured a feature&apos;s real cost yet — better to show &quot;—&quot; than fake a 100%
        margin.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {Object.entries(draft).map(([k, v]) => (
          <div key={k}>
            <label className="text-[10px] font-mono uppercase tracking-widest text-muted block mb-1">{k}</label>
            <input
              type="number"
              step="0.001"
              min="0"
              value={v}
              onChange={(e) => update(k, e.target.value)}
              placeholder="—"
              className="input-brutal text-xs py-1.5 w-full"
              data-testid={`cost-input-${k}`}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-end">
        <button
          type="button"
          onClick={submit}
          disabled={saving}
          className="btn-brutal text-xs disabled:opacity-40"
          data-testid="cost-save-btn"
        >
          {saving ? "Saving…" : "Save cost table"}
        </button>
      </div>
    </div>
  );
}

export default function AdminCostTelemetry() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(30);
  const [profit, setProfit] = useState(null);
  const [contribution, setContribution] = useState(null);
  const [lossMaking, setLossMaking] = useState(null);
  const [costConfig, setCostConfig] = useState({ values: {} });
  const [loading, setLoading] = useState(true);
  const [savingCosts, setSavingCosts] = useState(false);
  const [showEditor, setShowEditor] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, c, lm, cfg] = await Promise.all([
        api.get(`/admin/cost-telemetry/profit-per-feature?days=${days}`),
        api.get(`/admin/cost-telemetry/contribution-by-source?days=${days}`),
        api.get(`/admin/cost-telemetry/loss-making?days=${days}&top_n=10`),
        api.get("/admin/cost-telemetry/cost-config"),
      ]);
      setProfit(p.data);
      setContribution(c.data);
      setLossMaking(lm.data);
      setCostConfig(cfg.data || { values: {} });
    } catch (e) {
      setError(e?.response?.data?.detail?.message || "Could not load cost telemetry.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!authLoading && user?.role === "admin") {
      Promise.resolve().then(load);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user, days]);

  const saveCosts = async (values) => {
    setSavingCosts(true);
    try {
      await api.post("/admin/cost-telemetry/cost-config", { values });
      toast.success("Cost table saved.");
      setShowEditor(false);
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail?.message || "Could not save costs.");
    } finally {
      setSavingCosts(false);
    }
  };

  const featuresConfigured = useMemo(() => Object.keys(costConfig?.values || {}).length, [costConfig]);

  if (authLoading) return (
    <div className="min-h-screen page-bg"><Navbar /><div className="max-w-6xl mx-auto p-8 text-muted font-mono text-sm">Loading…</div></div>
  );
  if (!user) return <Navigate to="/login?redirect=/admin/cost-telemetry" replace />;
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen page-bg">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-16">
          <div className="brutal-card p-8 border-rose/40 bg-rose-500/10" data-testid="ct-forbidden">
            <div className="text-rose-300 font-mono text-xs uppercase tracking-widest mb-3">403 · admin only</div>
            <p className="text-sm">This dashboard is for operators.</p>
            <div className="mt-4"><Link to="/dashboard" className="btn-brutal text-sm">Back</Link></div>
          </div>
        </div>
      </div>
    );
  }

  const t = profit?.totals;
  const cTot = contribution?.totals;

  return (
    <div className="min-h-screen page-bg" data-testid="admin-cost-telemetry-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 sm:py-12 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">ANALYTICS · COST TELEMETRY</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Profit per feature.</h1>
          <p className="text-sm text-muted max-w-2xl">
            Revenue is half the story. This page joins credits consumed × your
            configured cost per credit to show which features earn money and
            which features just spend it. Legacy rows without tags bucket
            under <code className="font-mono text-amber/80">unknown</code>.
          </p>
          <div className="flex items-center gap-3 pt-2 flex-wrap">
            <div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-muted">
              <span>Window:</span>
              {WINDOW_OPTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDays(d)}
                  className={`px-2.5 py-1 rounded-md border text-xs font-mono ${
                    days === d ? "bg-amber/20 border-amber/50 text-amber" : "bg-white/[0.02] border-white/10 text-ink/70 hover:bg-white/[0.06]"
                  }`}
                  data-testid={`ct-window-${d}`}
                >
                  {d}d
                </button>
              ))}
            </div>
            <button onClick={load} className="btn-ghost text-xs" disabled={loading} data-testid="ct-refresh">
              {loading ? "Loading…" : "Refresh"}
            </button>
            <button onClick={() => setShowEditor((s) => !s)} className="btn-ghost text-xs" data-testid="ct-edit-costs-btn">
              {showEditor ? "Hide cost editor" : `Configure costs (${featuresConfigured}/7)`}
            </button>
          </div>
          {error && (
            <div className="brutal-card p-3 border-rose/40 bg-rose-500/10 text-rose-300 text-xs" data-testid="ct-error">{error}</div>
          )}
        </header>

        {showEditor && (
          <FeatureCostEditor
            values={costConfig.values || {}}
            onSave={saveCosts}
            saving={savingCosts}
          />
        )}

        {profit && (
          <>
            {/* Top tiles */}
            <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3" data-testid="ct-summary-tiles">
              <Tile label={`Revenue · ${days}d`} value={inr(profit.total_revenue_inr)} tone="text-emerald-300" testId="ct-tile-revenue" />
              <Tile label="Credits consumed" value={Number(profit.total_credits_consumed).toLocaleString("en-IN")} testId="ct-tile-credits" />
              <Tile label="Estimated cost" value={inr(t?.estimated_cost_inr)} tone="text-rose-soft" testId="ct-tile-cost"
                sub={t?.all_features_costed ? null : "Partial — some features uncosted"} />
              <Tile label="Gross profit" value={inr(t?.gross_profit_inr)} tone={t?.gross_profit_inr >= 0 ? "text-emerald-300" : "text-rose-soft"} testId="ct-tile-profit" />
              <Tile label="Overall margin" value={pct(t?.margin_pct)} tone={(t?.margin_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-soft"} testId="ct-tile-margin" />
            </section>

            {/* Profit per feature table */}
            <section className="space-y-3" data-testid="ct-profit-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Profit per feature</h2>
              <div className="brutal-card overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                      <th className="p-3">Feature</th>
                      <th className="p-3 text-right">Usage</th>
                      <th className="p-3 text-right">Credits</th>
                      <th className="p-3 text-right">Share</th>
                      <th className="p-3 text-right">Est. cost</th>
                      <th className="p-3 text-right">Revenue (attr.)</th>
                      <th className="p-3 text-right">Gross profit</th>
                      <th className="p-3 text-right">Margin</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profit.rows.map((row) => (
                      <tr key={row.feature} className="border-t border-white/5" data-testid={`ct-row-${row.feature}`}>
                        <td className={`p-3 font-mono ${FEATURE_TONE[row.feature] || ""}`}>{row.feature}</td>
                        <td className="p-3 text-right font-mono">{row.usage_count.toLocaleString("en-IN")}</td>
                        <td className="p-3 text-right font-mono">{row.credits_consumed.toLocaleString("en-IN")}</td>
                        <td className="p-3 text-right font-mono text-muted">{row.share_of_credits_pct}%</td>
                        <td className="p-3 text-right font-mono">
                          {row.cost_source === "not_configured" ? (
                            <span className="text-amber/70" title="No cost configured for this feature">—</span>
                          ) : (
                            inr(row.estimated_cost_inr)
                          )}
                          <div className={`text-[9px] font-mono ${row.cost_source === "provider_metered" ? "text-emerald-300" : "text-muted"}`}>
                            {row.cost_source === "provider_metered" ? `metered · ${row.metered_calls} call${row.metered_calls === 1 ? "" : "s"}` : row.cost_source === "configured" ? "est." : ""}
                          </div>
                        </td>
                        <td className="p-3 text-right font-mono">{inr(row.revenue_attributed_inr)}</td>
                        <td className={`p-3 text-right font-mono font-bold ${row.gross_profit_inr == null ? "" : row.gross_profit_inr >= 0 ? "text-emerald-300" : "text-rose-soft"}`}>
                          {inr(row.gross_profit_inr)}
                        </td>
                        <td className={`p-3 text-right font-mono ${(row.margin_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-soft"}`}>
                          {pct(row.margin_pct)}
                        </td>
                      </tr>
                    ))}
                    <tr className="border-t-2 border-white/10 bg-white/[0.02]" data-testid="ct-row-total">
                      <td className="p-3 font-mono font-bold uppercase text-[11px] tracking-widest">Total</td>
                      <td className="p-3 text-right font-mono font-bold">{t?.usage_count?.toLocaleString("en-IN")}</td>
                      <td className="p-3 text-right font-mono font-bold">{t?.credits_consumed?.toLocaleString("en-IN")}</td>
                      <td className="p-3"></td>
                      <td className="p-3 text-right font-mono">{inr(t?.estimated_cost_inr)}</td>
                      <td className="p-3 text-right font-mono">{inr(t?.revenue_attributed_inr)}</td>
                      <td className={`p-3 text-right font-mono font-bold ${(t?.gross_profit_inr ?? 0) >= 0 ? "text-emerald-300" : "text-rose-soft"}`}>{inr(t?.gross_profit_inr)}</td>
                      <td className={`p-3 text-right font-mono font-bold ${(t?.margin_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-soft"}`}>{pct(t?.margin_pct)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] font-mono text-muted">
                Revenue is apportioned to features by their share of credits consumed.
                Cost &quot;—&quot; means no cost-per-credit configured for that feature yet.
                <button onClick={() => setShowEditor(true)} className="text-amber underline ml-1.5">Configure now</button>
              </p>
            </section>
          </>
        )}

        {lossMaking && (
          <>
            {/* ── Loss-making summary ─────────────────────────── */}
            <section className="space-y-3" data-testid="lm-summary-section">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Loss-making requests · {days}d</h2>
              {lossMaking.validation?.has_suspect_data && (
                <div className="brutal-card p-4 border-amber/50 bg-amber-500/10" data-testid="lm-validation-banner">
                  <div className="text-amber font-mono text-[10px] uppercase tracking-widest mb-1">
                    ⚠ TELEMETRY VALIDATION WARNING — {lossMaking.validation.suspect_rows_count} suspect row{lossMaking.validation.suspect_rows_count === 1 ? "" : "s"}
                  </div>
                  <p className="text-xs text-ink/90">{lossMaking.validation.warning}</p>
                  <details className="mt-2 text-[11px]">
                    <summary className="cursor-pointer text-amber/80 font-mono">Show sample suspect rows</summary>
                    <ul className="mt-1.5 space-y-1 pl-4">
                      {(lossMaking.validation.suspect_rows_sample || []).map((s) => (
                        <li key={s.cost_id} className="font-mono text-muted">
                          {s.feature} · cost {inr(s.metered_cost_inr)} · credits {s.credits_deducted} · margin {s.margin_pct}%
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
              )}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Tile label="Requests analyzed" value={lossMaking.summary.total_requests_analyzed} testId="lm-tile-analyzed" />
                <Tile label="Flagged (warning + critical)" value={lossMaking.summary.total_flagged}
                  tone={lossMaking.summary.total_flagged > 0 ? "text-rose-soft" : "text-emerald-300"}
                  testId="lm-tile-flagged" />
                <Tile label="Total negative margin" value={inr(lossMaking.summary.total_negative_margin_inr)}
                  tone={(lossMaking.summary.total_negative_margin_inr ?? 0) < 0 ? "text-rose-soft" : "text-emerald-300"}
                  testId="lm-tile-neg-margin" />
                <Tile label="Revenue / credit" value={`₹${lossMaking.revenue_per_credit_inr}`}
                  sub="apportioned" testId="lm-tile-rev-per-credit" />
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {/* By severity */}
                <div className="brutal-card p-4" data-testid="lm-by-severity">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">By severity</div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    {Object.entries(lossMaking.summary.by_severity).map(([sev, count]) => {
                      const tone = sev === "critical" ? "text-rose-soft" : sev === "warning" ? "text-amber" : sev === "info" ? "text-sky-300" : sev === "ok" ? "text-emerald-300" : "text-muted";
                      return (
                        <div key={sev} className="flex justify-between border-b border-white/5 py-1.5">
                          <span className={`font-mono uppercase text-[11px] tracking-widest ${tone}`}>{sev}</span>
                          <span className="font-mono font-bold">{count}</span>
                        </div>
                      );
                    })}
                  </div>
                  <p className="text-[10px] font-mono text-muted mt-2">
                    Critical: margin &lt; -20% · Warning: margin &lt; 0% · Info: margin &lt; 10% · OK: margin ≥ 10%
                  </p>
                </div>

                {/* By feature */}
                <div className="brutal-card p-4" data-testid="lm-by-feature">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">By feature</div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                        <th className="py-1">Feature</th>
                        <th className="py-1 text-right">Requests</th>
                        <th className="py-1 text-right">Cost</th>
                        <th className="py-1 text-right">Margin</th>
                        <th className="py-1 text-right">Flagged</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lossMaking.summary.by_feature.map((b) => (
                        <tr key={b.feature} className="border-t border-white/5">
                          <td className={`py-1 font-mono ${FEATURE_TONE[b.feature] || ""}`}>{b.feature}</td>
                          <td className="py-1 text-right font-mono">{b.requests}</td>
                          <td className="py-1 text-right font-mono">{inr(b.cost_inr)}</td>
                          <td className={`py-1 text-right font-mono font-bold ${b.margin_inr >= 0 ? "text-emerald-300" : "text-rose-soft"}`}>{inr(b.margin_inr)}</td>
                          <td className={`py-1 text-right font-mono ${b.flagged > 0 ? "text-rose-soft" : "text-muted"}`}>{b.flagged}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            {/* ── Top 10 Most Expensive Requests ────────────── */}
            <section className="space-y-3" data-testid="lm-top-expensive">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Top 10 most expensive requests</h2>
              {lossMaking.top_expensive.length === 0 ? (
                <div className="brutal-card p-4 text-sm text-muted" data-testid="lm-expensive-empty">No metered requests in window.</div>
              ) : (
                <div className="brutal-card overflow-x-auto">
                  <RequestTable rows={lossMaking.top_expensive} testIdPrefix="lm-expensive" />
                </div>
              )}
            </section>

            {/* ── Top 10 Biggest Losses ────────────────────── */}
            <section className="space-y-3" data-testid="lm-top-losses">
              <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Top 10 biggest losses</h2>
              {lossMaking.top_losses.length === 0 ? (
                <div className="brutal-card p-4 text-sm text-emerald-300" data-testid="lm-losses-empty">
                  ✓ No loss-making requests in this window. Margins are positive everywhere.
                </div>
              ) : (
                <div className="brutal-card overflow-x-auto">
                  <RequestTable rows={lossMaking.top_losses} testIdPrefix="lm-losses" />
                </div>
              )}
            </section>
          </>
        )}

        {contribution && (
          <section className="space-y-3" data-testid="ct-contribution-section">
            <h2 className="text-[11px] font-mono uppercase tracking-widest text-muted">Feature contribution to revenue (by entry point)</h2>
            <p className="text-xs text-muted -mt-1.5">
              Which CTA actually delivers paying subscribers. Visits come from <code className="font-mono text-amber/80">funnel_events.pricing_visit_source</code>;
              checkouts and revenue come from <code className="font-mono text-amber/80">payment_orders</code>.
            </p>
            <div className="brutal-card overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[10px] font-mono uppercase tracking-widest text-muted text-left">
                    <th className="p-3">Source</th>
                    <th className="p-3 text-right">Pricing visits</th>
                    <th className="p-3 text-right">Checkout starts</th>
                    <th className="p-3 text-right">Paid</th>
                    <th className="p-3 text-right">Conversion</th>
                    <th className="p-3 text-right">Revenue</th>
                    <th className="p-3 text-right">ARPPU</th>
                  </tr>
                </thead>
                <tbody>
                  {contribution.rows.length === 0 ? (
                    <tr><td colSpan={7} className="p-6 text-center text-muted text-xs" data-testid="ct-contribution-empty">No traffic in this window.</td></tr>
                  ) : contribution.rows.map((r) => (
                    <tr key={r.pricing_visit_source} className="border-t border-white/5" data-testid={`ct-source-${r.pricing_visit_source}`}>
                      <td className="p-3 font-mono">{r.pricing_visit_source}</td>
                      <td className="p-3 text-right font-mono">{r.visits.toLocaleString("en-IN")}</td>
                      <td className="p-3 text-right font-mono">{r.checkout_starts.toLocaleString("en-IN")}</td>
                      <td className="p-3 text-right font-mono text-emerald-300 font-bold">{r.paid_orders.toLocaleString("en-IN")}</td>
                      <td className={`p-3 text-right font-mono ${(r.conversion_pct ?? 0) > 0 ? "text-emerald-300" : "text-muted"}`}>{pct(r.conversion_pct)}</td>
                      <td className="p-3 text-right font-mono">{inr(r.revenue_inr)}</td>
                      <td className="p-3 text-right font-mono">{inr(r.arppu_inr)}</td>
                    </tr>
                  ))}
                  <tr className="border-t-2 border-white/10 bg-white/[0.02]" data-testid="ct-source-total">
                    <td className="p-3 font-mono font-bold uppercase text-[11px] tracking-widest">Total</td>
                    <td className="p-3 text-right font-mono font-bold">{cTot?.visits?.toLocaleString("en-IN")}</td>
                    <td className="p-3 text-right font-mono font-bold">{cTot?.checkout_starts?.toLocaleString("en-IN")}</td>
                    <td className="p-3 text-right font-mono font-bold text-emerald-300">{cTot?.paid_orders?.toLocaleString("en-IN")}</td>
                    <td className="p-3 text-right font-mono">{pct(cTot?.conversion_pct)}</td>
                    <td className="p-3 text-right font-mono font-bold">{inr(cTot?.revenue_inr)}</td>
                    <td className="p-3 text-right font-mono">{inr(cTot?.arppu_inr)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        )}

        <footer className="text-[11px] font-mono uppercase tracking-widest text-muted pt-6 border-t border-white/5" data-testid="ct-footer">
          Reproducible from credit_events + funnel_events + payment_orders · Legacy rows bucket to &quot;unknown&quot; via $ifNull
        </footer>
      </div>
    </div>
  );
}
