/**
 * Payment return page.
 *
 * Behavior contract (locked by tests/test_payment_return_redirect.py):
 *   - Initial render shows a neutral "Checking your order…" state — never
 *     the optimistic "Confirming your payment…" copy, which is reserved for
 *     orders where a payment attempt is actively settling.
 *   - We immediately hit /api/payments/order/:id (which itself re-queries
 *     Cashfree if the local row is still `created`/`active`).
 *   - Terminal-and-paid → success card.
 *   - Terminal-and-not-paid (unpaid / user_dropped / failed / expired /
 *     terminated) → bounce to /pricing with a "Payment was not completed.
 *     You can try again." toast. No "Confirming" screen ever.
 *   - Still pending after first read → "Confirming your payment…" while we
 *     poll every 2s for up to 30s. Webhook settlement typically lands inside
 *     this window.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import api from "../lib/api";
import Navbar from "../components/Navbar";

// Statuses where the order is fully resolved
const TERMINAL_PAID = ["paid"];
const TERMINAL_NOT_PAID = ["unpaid", "user_dropped", "failed", "expired", "terminated"];
const isTerminal = (s) => TERMINAL_PAID.includes(s) || TERMINAL_NOT_PAID.includes(s);

const NOT_COMPLETED_TOAST = "Payment was not completed. You can try again.";

export default function PaymentReturn() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const orderId = params.get("order_id");

  const [order, setOrder] = useState(null);
  const [phase, setPhase] = useState("initial"); // initial → pending → terminal | timeout | error
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!orderId) {
      setError("No order id in the return URL.");
      setPhase("error");
      return;
    }
    let cancelled = false;
    let elapsed = 0;
    let firstRead = true;

    const finishNotPaid = (status) => {
      // Redirect back to pricing with a clear, non-alarming toast.
      // We delay the navigate just long enough for the toast to register.
      toast.error(NOT_COMPLETED_TOAST);
      // Log the resolved status to console for debug-ability (admin-side
      // observability already covered by /admin/webhook-logs).
      // eslint-disable-next-line no-console
      console.info("[payment-return] order finished without payment:", status);
      setTimeout(() => {
        if (!cancelled) navigate("/pricing?source=pay_return_retry", { replace: true });
      }, 250);
    };

    const poll = async () => {
      try {
        const { data } = await api.get(`/payments/order/${orderId}`);
        if (cancelled) return;
        setOrder(data.order);
        const status = data?.order?.status;

        if (TERMINAL_PAID.includes(status)) {
          setPhase("terminal");
          return;
        }
        if (TERMINAL_NOT_PAID.includes(status)) {
          setPhase("terminal");
          finishNotPaid(status);
          return;
        }
        // Still pending — only NOW are we allowed to show the "Confirming…" copy
        if (firstRead) {
          firstRead = false;
          setPhase("pending");
        }
        elapsed += 2;
        if (elapsed >= 30) {
          setPhase("timeout");
          return;
        }
        setTimeout(poll, 2000);
      } catch (e) {
        if (cancelled) return;
        const detail = e?.response?.data?.detail;
        setError(typeof detail === "string" ? detail : "Could not fetch order status.");
        setPhase("error");
      }
    };

    poll();
    return () => { cancelled = true; };
  }, [orderId, navigate]);

  const isPaid = order?.status === "paid";

  return (
    <div className="min-h-screen page-bg" data-testid="payment-return-page">
      <Navbar />
      <div className="max-w-lg mx-auto px-4 sm:px-8 py-14 space-y-6">
        {phase === "initial" && (
          <div className="brutal-card p-6 text-center" data-testid="payment-return-checking">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">CHECKING</div>
            <h1 className="font-display text-2xl font-bold mb-2">Checking your order…</h1>
            <p className="text-sm text-muted">One moment.</p>
          </div>
        )}

        {phase === "pending" && !isPaid && (
          <div className="brutal-card p-6 text-center" data-testid="payment-return-pending">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">PROCESSING</div>
            <h1 className="font-display text-2xl font-bold mb-2">Confirming your payment…</h1>
            <p className="text-sm text-muted">We're checking with the payment gateway directly. This takes a few seconds.</p>
          </div>
        )}

        {isPaid && (
          <div className="brutal-card p-6 border-emerald/40 bg-emerald-500/10" data-testid="payment-return-paid">
            <div className="text-emerald-300 font-mono text-[11px] uppercase tracking-widest mb-2">PAID</div>
            <h1 className="font-display text-2xl font-bold mb-1">{order.credits.toLocaleString("en-IN")} credits added.</h1>
            <p className="text-sm text-muted mb-4">Your plan is now <span className="text-ink font-mono">{order.plan_id}</span>. The credits are already in your balance.</p>
            <div className="flex gap-2 flex-wrap">
              <Link to="/dashboard" className="btn-brutal text-sm" data-testid="payment-return-continue">Continue to dashboard</Link>
              <Link to="/pricing?source=pay_return_retry" className="btn-ghost text-sm" data-testid="payment-return-pricing">View plans</Link>
            </div>
          </div>
        )}

        {phase === "timeout" && !isPaid && (
          <div className="brutal-card p-6" data-testid="payment-return-still-pending">
            <div className="text-amber font-mono text-[11px] uppercase tracking-widest mb-2">STILL PROCESSING</div>
            <h1 className="font-display text-xl font-bold mb-1">We haven't received confirmation yet.</h1>
            <p className="text-sm text-muted">The payment gateway is taking longer than usual. If you completed payment, your credits will appear automatically once it confirms.</p>
            <div className="flex gap-2 flex-wrap mt-4">
              <Link to="/dashboard" className="btn-ghost text-sm">Back to dashboard</Link>
              <Link to="/pricing?source=pay_return_retry" className="btn-ghost text-sm">Pricing</Link>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div className="brutal-card p-6 border-rose/40 bg-rose-500/10" data-testid="payment-return-error">
            <div className="text-rose-300 font-mono text-[11px] uppercase tracking-widest mb-2">ERROR</div>
            <h1 className="font-display text-xl font-bold mb-1">Could not load the order</h1>
            <p className="text-sm text-muted">{error}</p>
            <Link to="/pricing?source=pay_return_retry" className="btn-ghost text-sm mt-4 inline-block">Back to pricing</Link>
          </div>
        )}
      </div>
    </div>
  );
}
