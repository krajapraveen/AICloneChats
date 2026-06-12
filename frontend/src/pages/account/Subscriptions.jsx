import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../../lib/api";

function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function StatusBadge({ status }) {
  const palette =
    status === "paid" ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10" :
    status === "failed" ? "border-rose/40 text-rose-soft bg-rose/10" :
    status === "refunded" ? "border-amber/40 text-amber bg-amber/10" :
    "border-white/15 text-ink/75 bg-white/[0.03]";
  return (
    <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${palette}`}>
      {status || "—"}
    </span>
  );
}

export default function Subscriptions() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancel = false;
    api.get("/me/orders")
      .then((r) => !cancel && setData(r.data || {}))
      .catch(() => !cancel && setData({ items: [], current_plan_name: "Free" }))
      .finally(() => !cancel && setLoading(false));
    return () => { cancel = true; };
  }, []);

  if (loading) {
    return <section data-testid="subscriptions-loading"><p className="text-sm text-muted">Loading your purchases…</p></section>;
  }

  const orders = data?.items || [];

  return (
    <section data-testid="subscriptions-section">
      <h2 className="heading-display text-2xl mb-1">Manage Subscriptions</h2>
      <p className="text-sm text-muted mb-6">Your current plan and every credit pack you've purchased.</p>

      {/* Current plan card */}
      <div className="brutal-card p-6 mb-6" data-testid="current-plan-card">
        <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1">Current plan</div>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="font-display text-2xl" data-testid="current-plan-name">
              {data?.current_plan_name || "Free"}
            </div>
            <div className="text-sm text-muted mt-1">
              {data?.admin_unlimited ? (
                <span className="text-violet-soft">∞ Admin unlimited credits</span>
              ) : (
                <>Credits balance: <span className="text-ink font-medium" data-testid="current-credits">{data?.credits_balance ?? 0}</span></>
              )}
            </div>
          </div>
          <Link to="/pricing" className="btn-brutal text-xs" data-testid="manage-plan-btn">
            {data?.current_plan_id && data.current_plan_id !== "free" ? "Change plan" : "Choose a plan"}
          </Link>
        </div>
      </div>

      {/* Order history */}
      <div className="brutal-card p-6" data-testid="orders-card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="heading-display text-lg">Purchase history</h3>
          <span className="text-[10px] font-mono uppercase tracking-widest text-muted">{orders.length} order{orders.length === 1 ? "" : "s"}</span>
        </div>

        {orders.length === 0 ? (
          <div className="text-sm text-muted py-4" data-testid="orders-empty">
            No purchases yet. Pick a plan on <Link to="/pricing" className="text-amber underline">/pricing</Link> to get started.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[10px] font-mono uppercase tracking-widest text-amber/80 border-b border-white/10 text-left">
                  <th className="py-2 pr-3">When</th>
                  <th className="py-2 pr-3">Item</th>
                  <th className="py-2 pr-3">Credits</th>
                  <th className="py-2 pr-3">Amount</th>
                  <th className="py-2 pr-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.order_id} className="border-b border-white/5 last:border-0" data-testid={`order-row-${o.order_id}`}>
                    <td className="py-2.5 pr-3 text-ink/85 whitespace-nowrap">{formatDate(o.paid_at || o.created_at)}</td>
                    <td className="py-2.5 pr-3 text-ink/85">{o.plan_id || o.pack_id || "—"}</td>
                    <td className="py-2.5 pr-3 text-ink/85">{o.credits_to_grant ?? "—"}</td>
                    <td className="py-2.5 pr-3 text-ink/85 whitespace-nowrap">
                      {o.amount != null ? `${(o.amount / 100).toFixed(2)} ${o.currency || "INR"}` : "—"}
                    </td>
                    <td className="py-2.5 pr-3"><StatusBadge status={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-muted mt-4">
          Need a refund? Email <a href="mailto:admin@aiclonechats.com?subject=REFUND" className="text-amber underline">admin@aiclonechats.com</a> with subject <code>REFUND</code> and the order ID.
        </p>
      </div>
    </section>
  );
}
