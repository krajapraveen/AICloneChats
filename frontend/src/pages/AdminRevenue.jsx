/**
 * AdminRevenue — read-only revenue & funnel mirror.
 *
 * Rules baked into this page:
 *   - No interpretation, no recommendations, no automated actions.
 *   - Six sections only: Funnel · Revenue · Credit Economy · Emotional Gravity · Cohorts · Operational Health.
 *   - Mobile-readable; built for the 30-second comprehension test at 11pm.
 *   - Every section has a CSV download button.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { API } from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const SECTIONS = [
  { key: "funnel", title: "1. Funnel", endpoint: "/admin/revenue/funnel", defaultWindow: 30, windowLabel: "days" },
  { key: "revenue", title: "2. Revenue", endpoint: "/admin/revenue/revenue", defaultWindow: 30, windowLabel: "days" },
  { key: "credit", title: "3. Credit Economy", endpoint: "/admin/revenue/credit-economy", defaultWindow: 30, windowLabel: "days" },
  { key: "gravity", title: "4. Emotional Gravity", endpoint: "/admin/revenue/emotional-gravity", defaultWindow: 90, windowLabel: "days" },
  { key: "cohorts", title: "5. Cohorts", endpoint: "/admin/revenue/cohorts", defaultWindow: 12, windowLabel: "weeks", queryKey: "weeks" },
  { key: "ops", title: "6. Operational Health", endpoint: "/admin/revenue/operational-health", defaultWindow: 30, windowLabel: "days" },
];

function downloadCsv(endpoint, queryKey, value) {
  const token = localStorage.getItem("session_token");
  const sep = endpoint.includes("?") ? "&" : "?";
  const url = `${API}${endpoint}${sep}${queryKey || "days"}=${value}&format=csv`;
  fetch(url, { headers: { Authorization: `Bearer ${token}` } })
    .then((r) => r.blob())
    .then((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = endpoint.split("/").pop() + ".csv";
      a.click();
    })
    .catch(() => toast.error("CSV download failed"));
}

function Stat({ label, value, sub, testid }) {
  return (
    <div className="glass-card p-3" data-testid={testid}>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display text-2xl font-bold mt-0.5">{value ?? "—"}</div>
      {sub != null && <div className="text-[11px] font-mono text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function Table({ columns, rows, emptyHint, testid }) {
  if (!rows || rows.length === 0) {
    return <div className="text-xs font-mono text-muted py-3" data-testid={testid + "-empty"}>{emptyHint || "no rows in this window"}</div>;
  }
  return (
    <div className="overflow-x-auto" data-testid={testid}>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-muted uppercase tracking-widest text-[10px]">
            {columns.map((c) => (
              <th key={c.key} className="text-left py-2 pr-4 whitespace-nowrap">{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-white/5">
              {columns.map((c) => (
                <td key={c.key} className="py-2 pr-4 whitespace-nowrap text-ink/85">{c.render ? c.render(r) : r[c.key] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SectionHeader({ title, windowValue, setWindowValue, windowLabel, onCsv }) {
  return (
    <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
      <h2 className="heading-display text-xl" data-testid={`section-title-${title.split(".")[0]}`}>{title}</h2>
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-mono uppercase tracking-widest text-muted">Window</span>
        <select
          value={windowValue}
          onChange={(e) => setWindowValue(Number(e.target.value))}
          className="input-brutal text-[11px] py-1 px-2"
          data-testid={`window-select-${title.split(".")[0]}`}
        >
          {(windowLabel === "weeks" ? [4, 8, 12, 24, 52] : [7, 14, 30, 60, 90, 180, 365]).map((d) => (
            <option key={d} value={d}>{d} {windowLabel}</option>
          ))}
        </select>
        <button onClick={onCsv} className="btn-ghost text-[11px]" data-testid={`csv-${title.split(".")[0]}`}>Export CSV</button>
      </div>
    </div>
  );
}

// ============================================================================
// Section renderers — pure data display, NO interpretation.
// ============================================================================
function FunnelSection({ data }) {
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {data.steps.map((s) => (
          <Stat key={s.step} label={s.step.replace(/_/g, " ")} value={s.value} sub={s.secondary ? Object.entries(s.secondary).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ") : null} testid={`funnel-step-${s.step}`} />
        ))}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        {Object.entries(data.conversion_pct).map(([k, v]) => (
          <Stat key={k} label={k.replace(/_/g, " → ")} value={`${v}%`} testid={`funnel-pct-${k}`} />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="topup buyers" value={data.topup_repeat.buyers} testid="topup-buyers" />
        <Stat label="topup repeat buyers" value={data.topup_repeat.repeat_buyers} testid="topup-repeat-buyers" />
        <Stat label="topup repeat rate" value={`${data.topup_repeat.repeat_rate_pct}%`} testid="topup-repeat-rate" />
      </div>
    </div>
  );
}

function RevenueSection({ data }) {
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="MRR (INR)" value={`₹${data.mrr_inr.toLocaleString("en-IN")}`} testid="mrr-inr" />
        <Stat label="Subscription rev (window)" value={`₹${data.subscription_revenue_window_inr.toLocaleString("en-IN")}`} testid="sub-rev" />
        <Stat label="Top-up rev (window)" value={`₹${data.topup_revenue_window_inr.toLocaleString("en-IN")}`} testid="topup-rev" />
        <Stat label="ARPU (window)" value={`₹${data.arpu_inr_window}`} sub={`${data.paying_users_window} paying users`} testid="arpu" />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <Stat label="Refunds succeeded" value={data.refunds_window.succeeded_amount} testid="refund-amount" />
        <Stat label="Chargebacks (window)" value={data.chargebacks_window} testid="chargebacks" />
        <Stat label="Active subscriptions" value={(data.active_subscriptions_by_plan || []).reduce((a, b) => a + b.active_users, 0)} testid="active-subs" />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Active subscriptions by plan</div>
        <Table
          testid="active-by-plan-table"
          columns={[
            { key: "plan_name", label: "Plan" },
            { key: "active_users", label: "Users" },
            { key: "monthly_price_inr", label: "₹/mo", render: (r) => `₹${r.monthly_price_inr}` },
            { key: "revenue_inr", label: "MRR (₹)", render: (r) => `₹${r.revenue_inr.toLocaleString("en-IN")}` },
          ]}
          rows={data.active_subscriptions_by_plan}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Top-up revenue by pack (window)</div>
        <Table
          testid="topup-by-pack-table"
          columns={[
            { key: "pack_id", label: "Pack" },
            { key: "orders", label: "Orders" },
            { key: "credits_delivered", label: "Credits" },
            { key: "revenue_inr", label: "Revenue ₹", render: (r) => `₹${(r.revenue_inr || 0).toLocaleString("en-IN")}` },
          ]}
          rows={data.topup_revenue_window_by_pack}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Revenue by country / currency</div>
        <Table
          testid="rev-by-country-table"
          columns={[
            { key: "country_code", label: "Country" },
            { key: "display_currency", label: "Currency" },
            { key: "orders", label: "Orders" },
            { key: "revenue_in_display_currency", label: "Local rev" },
            { key: "revenue_in_inr", label: "INR settled", render: (r) => `₹${(r.revenue_in_inr || 0).toLocaleString("en-IN")}` },
          ]}
          rows={data.revenue_by_country}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Credit consumption by surface (window)</div>
        <Table
          testid="rev-by-surface-table"
          columns={[
            { key: "surface", label: "Surface" },
            { key: "events", label: "Events" },
            { key: "credits_consumed", label: "Credits consumed" },
          ]}
          rows={data.credit_consumption_by_surface}
        />
      </div>
    </div>
  );
}

function CreditEconomySection({ data }) {
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="Credits purchased" value={data.credits_purchased} testid="credits-purchased" />
        <Stat label="Credits consumed" value={data.credits_consumed} testid="credits-consumed" />
        <Stat label="Credits refunded" value={data.credits_refunded} testid="credits-refunded" />
        <Stat label="Net outstanding (window)" value={data.net_outstanding_in_window} testid="credits-net" />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Burn rate by surface</div>
        <Table
          testid="burn-table"
          columns={[
            { key: "surface", label: "Surface" },
            { key: "events", label: "Events" },
            { key: "credits_consumed", label: "Credits consumed" },
            { key: "credit_cost_per_event", label: "Cost/event", render: (r) => r.credit_cost_per_event ?? "—" },
            { key: "refund_rate_pct", label: "Refund %", render: (r) => `${r.refund_rate_pct}%` },
          ]}
          rows={data.burn_by_surface}
        />
      </div>
    </div>
  );
}

function EmotionalGravitySection({ data }) {
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">First paid intent surface (first 402 hit per user)</div>
        <Table
          testid="first-intent-table"
          columns={[{ key: "surface", label: "Surface" }, { key: "users", label: "Unique users" }]}
          rows={data.first_paid_intent_surface}
          emptyHint="no paywall hits yet — log a paywall to start populating"
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">First successful payment surface</div>
        <Table
          testid="first-paid-table"
          columns={[{ key: "surface", label: "Surface" }, { key: "users", label: "Unique users" }]}
          rows={data.first_successful_payment_surface}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Repeat-return surface (events on day 2+ of activity)</div>
        <Table
          testid="repeat-return-table"
          columns={[{ key: "surface", label: "Surface" }, { key: "events_on_return_days", label: "Events" }]}
          rows={data.repeat_return_surface}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Longest-session surface (messages per thread)</div>
        <Table
          testid="longest-session-table"
          columns={[
            { key: "surface", label: "Surface" },
            { key: "threads", label: "Threads" },
            { key: "median_messages_per_thread", label: "Median msgs" },
            { key: "p90_messages_per_thread", label: "p90 msgs" },
            { key: "max_messages_in_thread", label: "Max msgs" },
          ]}
          rows={data.longest_session_surface}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Highest top-up correlation surface (most-used surface in 14d before topup)</div>
        <Table
          testid="topup-correlation-table"
          columns={[{ key: "surface", label: "Surface" }, { key: "topup_purchases_preceded_by_this_surface", label: "Top-ups preceded" }]}
          rows={data.highest_top_up_correlation_surface}
        />
      </div>
    </div>
  );
}

function CohortsSection({ data }) {
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">By acquisition week — D1/D7/D30 return</div>
        <Table
          testid="cohorts-table"
          columns={[
            { key: "cohort_week", label: "Cohort" },
            { key: "signups", label: "Signups" },
            { key: "d1_return", label: "D1", render: (r) => `${r.d1_return} (${r.d1_pct}%)` },
            { key: "d7_return", label: "D7", render: (r) => `${r.d7_return} (${r.d7_pct}%)` },
            { key: "d30_return", label: "D30", render: (r) => `${r.d30_return} (${r.d30_pct}%)` },
          ]}
          rows={data.by_acquisition_week}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">By plan tier (total users)</div>
        <Table
          testid="cohort-plan-table"
          columns={[{ key: "plan_id", label: "Plan" }, { key: "users", label: "Users" }]}
          rows={data.by_plan_tier}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">By first paywall surface</div>
        <Table
          testid="cohort-first-paywall-table"
          columns={[{ key: "surface", label: "Surface" }, { key: "users", label: "Users" }]}
          rows={data.by_first_paywall_surface}
        />
      </div>
    </div>
  );
}

function OpsSection({ data }) {
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="Payment failure %" value={`${data.payment_failure_pct}%`} sub={`${data.payment_orders_failed}/${data.payment_orders_created}`} testid="pay-fail-pct" />
        <Stat label="Payment success %" value={`${data.payment_success_pct}%`} sub={`${data.payment_orders_paid}/${data.payment_orders_created}`} testid="pay-success-pct" />
        <Stat label="Webhook rejection %" value={`${data.webhook_rejection_pct}%`} sub={`${data.webhook_rejected}/${data.webhook_total}`} testid="wh-reject-pct" />
        <Stat label="Refund % of paid" value={`${data.refund_pct_of_paid}%`} sub={`${data.refunds} refunds`} testid="refund-pct" />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <Stat label="Chargeback %" value={`${data.chargeback_pct_of_paid}%`} sub={`${data.chargebacks} cases`} testid="cb-pct" />
        <Stat label="Avg response latency" value={data.avg_response_latency_ms_by_surface == null ? "not instrumented" : "—"} testid="latency" />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Webhook result breakdown</div>
        <Table
          testid="wh-breakdown-table"
          columns={[{ key: "result", label: "Result" }, { key: "n", label: "Count" }]}
          rows={data.webhook_result_breakdown}
        />
      </div>
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">AI failure refund rate by surface</div>
        <Table
          testid="ai-refund-rate-table"
          columns={[
            { key: "surface", label: "Surface" },
            { key: "deductions", label: "Deductions" },
            { key: "refunds", label: "Refunds" },
            { key: "ai_failure_refund_rate_pct", label: "Refund %", render: (r) => `${r.ai_failure_refund_rate_pct}%` },
          ]}
          rows={data.ai_failure_refund_rate_by_surface}
        />
      </div>
    </div>
  );
}

const RENDERERS = {
  funnel: FunnelSection,
  revenue: RevenueSection,
  credit: CreditEconomySection,
  gravity: EmotionalGravitySection,
  cohorts: CohortsSection,
  ops: OpsSection,
};


export default function AdminRevenue() {
  const { user, loading: authLoading } = useAuth();
  const [windows, setWindows] = useState(() => Object.fromEntries(SECTIONS.map((s) => [s.key, s.defaultWindow])));
  const [dataBySection, setDataBySection] = useState({});
  const [errBySection, setErrBySection] = useState({});

  useEffect(() => {
    if (authLoading || !user) return;
    SECTIONS.forEach(async (s) => {
      const qk = s.queryKey || "days";
      const val = windows[s.key];
      try {
        const { data } = await api.get(`${s.endpoint}?${qk}=${val}`);
        setDataBySection((m) => ({ ...m, [s.key]: data }));
        setErrBySection((m) => ({ ...m, [s.key]: null }));
      } catch (e) {
        const msg = e?.response?.status === 403 ? "Admin only" : (e?.response?.data?.detail || "fetch failed");
        setErrBySection((m) => ({ ...m, [s.key]: String(msg) }));
      }
    });
  }, [authLoading, user, windows]);

  if (authLoading) return null;
  if (!user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center px-4">
        <div className="glass-card p-6 max-w-md text-center">
          <h1 className="heading-display text-2xl mb-2">Admin only</h1>
          <p className="text-sm text-muted">Sign in as the admin account to view this page.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen" data-testid="admin-revenue-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 space-y-10">
        <header className="space-y-1">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">ADMIN · REVENUE MIRROR</div>
          <h1 className="heading-display text-3xl sm:text-4xl">What the platform is doing</h1>
          <p className="text-xs font-mono text-muted">Read-only. No interpretation. No recommendations. Refresh page to re-fetch.</p>
        </header>

        {SECTIONS.map((s) => {
          const Renderer = RENDERERS[s.key];
          const data = dataBySection[s.key];
          const err = errBySection[s.key];
          const qk = s.queryKey || "days";
          return (
            <section key={s.key} className="brutal-card p-4 sm:p-5" data-testid={`section-${s.key}`}>
              <SectionHeader
                title={s.title}
                windowValue={windows[s.key]}
                setWindowValue={(v) => setWindows((m) => ({ ...m, [s.key]: v }))}
                windowLabel={s.windowLabel}
                onCsv={() => downloadCsv(s.endpoint, qk, windows[s.key])}
              />
              {err ? (
                <div className="text-xs font-mono text-rose-300 py-2" data-testid={`error-${s.key}`}>error: {err}</div>
              ) : data ? (
                <Renderer data={data} />
              ) : (
                <div className="text-xs font-mono text-muted py-2">loading…</div>
              )}
            </section>
          );
        })}

        <footer className="text-[10px] font-mono uppercase tracking-widest text-muted pt-4 border-t border-white/5">
          Source: live aggregations against existing collections · No daily materialization yet · Avg response latency not instrumented at request layer
        </footer>
      </div>
    </div>
  );
}
