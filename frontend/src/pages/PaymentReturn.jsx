/**
 * Payment return page — polls /api/payments/order/:id for the AUTHORITATIVE
 * status. Never trusts the URL params Cashfree appends on return.
 *
 * Two polling phases:
 *   1. Immediate read (server may have already received webhook)
 *   2. If still `created` or `active`, poll every 2s up to 30s — the server
 *      re-fetches Cashfree on demand to side-step webhook delay.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";

const TERMINAL_STATUSES = ["paid", "failed", "expired", "terminated"];

export default function PaymentReturn() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const orderId = params.get("order_id");
  const [order, setOrder] = useState(null);
  const [polling, setPolling] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!orderId) {
      setError("No order id in the return URL.");
      setPolling(false);
      return;
    }
    let cancelled = false;
    let elapsed = 0;
    const poll = async () => {
      try {
        const { data } = await api.get(`/payments/order/${orderId}`);
        if (cancelled) return;
        setOrder(data.order);
        if (TERMINAL_STATUSES.includes(data.order.status)) {
          setPolling(false);
          return;
        }
        elapsed += 2;
        if (elapsed >= 30) {
          setPolling(false);
          return;
        }
        setTimeout(poll, 2000);
      } catch (e) {
        if (cancelled) return;
        setError(e?.response?.data?.detail || "Could not fetch order status.");
        setPolling(false);
      }
    };
    poll();
    return () => { cancelled = true; };
  }, [orderId]);

  const isPaid = order?.status === "paid";
  const isFailed = order && !isPaid && TERMINAL_STATUSES.includes(order.status);

  return (
    <div className="min-h-screen page-bg" data-testid="payment-return-page">
      <Navbar />
      <div className="max-w-lg mx-auto px-4 sm:px-8 py-14 space-y-6">
        {polling && !isPaid && !isFailed && (
          <div className="brutal-card p-6 text-center" data-testid="payment-return-pending">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">PROCESSING</div>
            <h1 className="font-display text-2xl font-bold mb-2">Confirming your payment…</h1>
            <p className="text-sm text-muted">We're checking with Cashfree directly. This takes a few seconds.</p>
          </div>
        )}

        {isPaid && (
          <div className="brutal-card p-6 border-emerald/40 bg-emerald-500/10" data-testid="payment-return-paid">
            <div className="text-emerald-300 font-mono text-[11px] uppercase tracking-widest mb-2">PAID</div>
            <h1 className="font-display text-2xl font-bold mb-1">{order.credits.toLocaleString("en-IN")} credits added.</h1>
            <p className="text-sm text-muted mb-4">Your plan is now <span className="text-ink font-mono">{order.plan_id}</span>. The credits are already in your balance.</p>
            <div className="flex gap-2 flex-wrap">
              <Link to="/dashboard" className="btn-brutal text-sm" data-testid="payment-return-continue">Continue to dashboard</Link>
              <Link to="/pricing" className="btn-ghost text-sm" data-testid="payment-return-pricing">View plans</Link>
            </div>
          </div>
        )}

        {isFailed && (
          <div className="brutal-card p-6 border-rose/40 bg-rose-500/10" data-testid="payment-return-failed">
            <div className="text-rose-300 font-mono text-[11px] uppercase tracking-widest mb-2">{order.status.toUpperCase()}</div>
            <h1 className="font-display text-2xl font-bold mb-1">Payment didn't complete.</h1>
            <p className="text-sm text-muted mb-4">
              {order.failure_reason ? `Reason: ${order.failure_reason}.` : "The transaction was not completed."} No credits were added and no money was taken.
              If your bank shows a deduction, it will be reversed within 5–7 business days.
            </p>
            <div className="flex gap-2 flex-wrap">
              <Link to="/pricing" className="btn-brutal text-sm" data-testid="payment-return-retry">Try a different plan</Link>
              <Link to="/dashboard" className="btn-ghost text-sm">Back to dashboard</Link>
            </div>
          </div>
        )}

        {error && !order && (
          <div className="brutal-card p-6 border-rose/40 bg-rose-500/10" data-testid="payment-return-error">
            <div className="text-rose-300 font-mono text-[11px] uppercase tracking-widest mb-2">ERROR</div>
            <h1 className="font-display text-xl font-bold mb-1">Could not load the order</h1>
            <p className="text-sm text-muted">{error}</p>
            <Link to="/pricing" className="btn-ghost text-sm mt-4 inline-block">Back to pricing</Link>
          </div>
        )}

        {!polling && !isPaid && !isFailed && !error && (
          <div className="brutal-card p-6" data-testid="payment-return-still-pending">
            <div className="text-amber font-mono text-[11px] uppercase tracking-widest mb-2">STILL PROCESSING</div>
            <h1 className="font-display text-xl font-bold mb-1">We haven't received confirmation yet.</h1>
            <p className="text-sm text-muted">Cashfree is taking longer than usual. Your credits will appear automatically once the payment confirms.</p>
            <Link to="/dashboard" className="btn-ghost text-sm mt-4 inline-block">Back to dashboard</Link>
          </div>
        )}
      </div>
    </div>
  );
}
