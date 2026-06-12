import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api from "../../lib/api";

function formatDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); }
  catch { return iso; }
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

// Visual tone per state — colours encode urgency, not the underlying domain.
const STATE_TONE = {
  active: "text-emerald-300",
  pending_cancellation: "text-amber",
  grace_period: "text-amber",
  payment_failed: "text-rose-soft",
  expired: "text-rose-soft",
  cancelled: "text-muted",
  refunded: "text-amber",
  deleted: "text-rose-soft",
  free: "text-ink/80",
  pending_verification: "text-amber",
};

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
  const [orders, setOrders] = useState(null);
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const [o, s] = await Promise.all([
        api.get("/me/orders"),
        api.get("/profile/subscription/state"),
      ]);
      setOrders(o.data || {});
      setState(s.data || null);
    } catch {
      setOrders({ items: [], current_plan_name: "Free" });
      setState(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reload(); }, []);

  const onCancel = async () => {
    if (!window.confirm("Cancel your subscription? You'll keep access until the end of the current period.")) return;
    setActionBusy(true);
    try {
      await api.post("/profile/subscription/cancel", { confirm: true });
      toast.success("Subscription scheduled to cancel at period end.");
      await reload();
    } catch (err) {
      toast.error(err?.response?.data?.detail?.message || "Could not cancel.");
    } finally {
      setActionBusy(false);
    }
  };

  const onResume = async () => {
    setActionBusy(true);
    try {
      await api.post("/profile/subscription/resume", {});
      toast.success("Subscription resumed.");
      await reload();
    } catch (err) {
      toast.error(err?.response?.data?.detail?.message || "Could not resume.");
    } finally {
      setActionBusy(false);
    }
  };

  if (loading) {
    return <section data-testid="subscriptions-loading"><p className="text-sm text-muted">Loading your purchases…</p></section>;
  }

  const orderList = orders?.items || [];
  const isAdminUnlimited = !!state?.admin_unlimited || !!orders?.admin_unlimited;
  const stateKey = state?.state || "free";
  const tone = STATE_TONE[stateKey] || "text-ink/80";

  return (
    <section data-testid="subscriptions-section">
      <h2 className="heading-display text-2xl mb-1">Manage Subscriptions</h2>
      <p className="text-sm text-muted mb-6">Your current plan, lifecycle state, and every credit pack you've purchased.</p>

      {/* Current plan card */}
      <div className="brutal-card p-6 mb-6" data-testid="current-plan-card">
        <div className="text-[10px] font-mono uppercase tracking-widest text-amber mb-1">Current plan</div>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="font-display text-2xl" data-testid="current-plan-name">
              {state?.current_plan_name || orders?.current_plan_name || "Free"}
            </div>
            <div className="text-sm text-muted mt-1">
              {isAdminUnlimited ? (
                <span className="text-violet-soft">∞ Admin unlimited credits</span>
              ) : (
                <>Credits balance: <span className="text-ink font-medium" data-testid="current-credits">{orders?.credits_balance ?? 0}</span></>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-2 items-end">
            <Link to="/pricing" className="btn-brutal text-xs" data-testid="manage-plan-btn">
              {state?.current_plan_id && state.current_plan_id !== "free" ? "Change plan" : "Choose a plan"}
            </Link>
            {state?.state === "active" && !isAdminUnlimited && (
              <button
                type="button"
                onClick={onCancel}
                disabled={actionBusy}
                className="btn-ghost text-[11px] text-rose-soft disabled:opacity-40"
                data-testid="cancel-subscription-btn"
              >
                Cancel subscription
              </button>
            )}
            {state?.state === "pending_cancellation" && (
              <button
                type="button"
                onClick={onResume}
                disabled={actionBusy}
                className="btn-ghost text-[11px] text-emerald-300 disabled:opacity-40"
                data-testid="resume-subscription-btn"
              >
                Resume subscription
              </button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-5 pt-5 border-t border-white/5">
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Status</div>
            <div className={`text-sm mt-1 font-medium ${isAdminUnlimited ? "text-violet-soft" : tone}`} data-testid="plan-status">
              {isAdminUnlimited ? "Admin · Unlimited" : (state?.state_label || "Free")}
            </div>
            {state?.state_reason && !isAdminUnlimited && (
              <div className="text-[10px] text-muted mt-1" data-testid="plan-state-reason">{state.state_reason}</div>
            )}
          </div>
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted">Started</div>
            <div className="text-sm mt-1 text-ink/85" data-testid="plan-started">{formatDate(state?.started_at) || "—"}</div>
          </div>
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted">
              {state?.state === "grace_period" ? "Grace until" : "Renews / Expires"}
            </div>
            <div className="text-sm mt-1 text-ink/85" data-testid="plan-expires">
              {formatDate(state?.grace_period_until && state?.state === "grace_period"
                ? state.grace_period_until
                : state?.expires_at) || "—"}
            </div>
          </div>
        </div>
      </div>

      {/* Order history */}
      <div className="brutal-card p-6" data-testid="orders-card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="heading-display text-lg">Purchase history</h3>
          <span className="text-[10px] font-mono uppercase tracking-widest text-muted">{orderList.length} order{orderList.length === 1 ? "" : "s"}</span>
        </div>

        {orderList.length === 0 ? (
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
                {orderList.map((o) => (
                  <tr key={o.order_id} className="border-b border-white/5 last:border-0" data-testid={`order-row-${o.order_id}`}>
                    <td className="py-2.5 pr-3 text-ink/85 whitespace-nowrap">{formatDateTime(o.paid_at || o.created_at)}</td>
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
