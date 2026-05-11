/**
 * Admin · Webhook Logs + Test Webhook tool
 *
 * Lets the operator:
 *  1. See every webhook arrival (real or simulated) with verdict
 *     (accepted / rejected_signature / rejected_replay / amount_mismatch / currency_mismatch / order_not_found)
 *  2. Fire a simulated, properly-signed webhook against the live endpoint
 *     for any existing order — useful for end-to-end smoke tests without
 *     needing to actually run a Cashfree-side test transaction.
 *  3. Fire tampered variants (bad signature, bad amount, bad currency,
 *     stale timestamp) and watch the rejection mechanics work in real time.
 */
import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const TAMPERS = [
  { value: "", label: "None (valid signed webhook)" },
  { value: "signature", label: "Tampered signature → expect 401" },
  { value: "amount", label: "Tampered amount → expect 400 amount_mismatch" },
  { value: "currency", label: "Tampered currency → expect 400 currency_mismatch" },
  { value: "timestamp", label: "Stale timestamp (>5min) → expect 400 replay" },
];

const RESULT_STYLE = {
  accepted: "border-emerald/40 bg-emerald-500/10 text-emerald-300",
  rejected_signature: "border-rose/40 bg-rose-500/10 text-rose-300",
  rejected_replay: "border-rose/40 bg-rose-500/10 text-rose-300",
  amount_mismatch: "border-rose/40 bg-rose-500/10 text-rose-300",
  currency_mismatch: "border-rose/40 bg-rose-500/10 text-rose-300",
  order_not_found: "border-amber/40 bg-amber-500/10 text-amber",
  duplicate_webhook_no_op: "border-amber/40 bg-amber-500/10 text-amber",
};

export default function AdminWebhookLogs() {
  const { user, loading: authLoading } = useAuth();
  const [logs, setLogs] = useState([]);
  const [orders, setOrders] = useState([]);
  const [resultFilter, setResultFilter] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [selectedOrder, setSelectedOrder] = useState("");
  const [tamper, setTamper] = useState("");
  const [lastTest, setLastTest] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user || user.role !== "admin") return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const logsUrl = `/admin/billing/webhook-logs?limit=100${resultFilter ? `&result=${encodeURIComponent(resultFilter)}` : ""}`;
        const [logsR, ordersR] = await Promise.all([
          api.get(logsUrl),
          api.get("/admin/billing/payments?limit=20"),
        ]);
        if (cancelled) return;
        setLogs(logsR.data?.logs || []);
        setOrders(ordersR.data?.payments || []);
        if (!selectedOrder && (ordersR.data?.payments || []).length) {
          setSelectedOrder(ordersR.data.payments[0].order_id);
        }
      } catch (e) {
        toast.error("Could not load webhook logs.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user, refreshKey, resultFilter, selectedOrder]);

  if (authLoading) {
    return <div className="min-h-screen page-bg"><Navbar /><div className="p-10 text-muted font-mono text-sm">Loading…</div></div>;
  }
  if (!user) return <Navigate to="/login?redirect=/admin/webhook-logs" replace />;
  if (user.role !== "admin") return <Navigate to="/dashboard" replace />;

  const fireTest = async () => {
    if (!selectedOrder) {
      toast.error("Pick an order first.");
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post("/admin/billing/test-webhook", {
        order_id: selectedOrder,
        event_type: "PAYMENT_SUCCESS_WEBHOOK",
        tamper: tamper || undefined,
      });
      setLastTest(data);
      if (data.ok) {
        toast.success(`Webhook delivered (200). ${tamper ? "Note: tampered variant was accepted unexpectedly." : ""}`);
      } else {
        toast.message(`Webhook returned ${data.status_code}. ${tamper ? "Rejection working as expected." : "Unexpected for valid signed webhook."}`);
      }
      setRefreshKey((k) => k + 1);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Test failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen page-bg" data-testid="admin-webhook-logs-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-8 space-y-8">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">OPERATIONS · WEBHOOKS</div>
          <h1 className="heading-display text-3xl">Cashfree Webhook Logs</h1>
          <p className="text-sm text-muted max-w-2xl">
            Every webhook arrival (real or simulated). Real arrivals will start flowing in once
            you register <span className="text-ink font-mono text-xs">{window.location.origin}/api/payments/webhook/cashfree</span>
            in the Cashfree merchant dashboard.
          </p>
        </header>

        <section className="brutal-card p-5 space-y-4" data-testid="webhook-test-card">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h2 className="font-display text-lg font-bold">Send a test webhook</h2>
              <p className="text-xs text-muted mt-1">Fires a properly-signed (or intentionally tampered) payload against the live endpoint. Same code path as a real Cashfree arrival.</p>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[11px] font-mono uppercase tracking-widest text-muted">Order</span>
              <select
                className="brutal-input mt-1 w-full"
                value={selectedOrder}
                onChange={(e) => setSelectedOrder(e.target.value)}
                data-testid="webhook-test-order-select"
              >
                {orders.length === 0 && <option value="">No orders yet — create one from /pricing first</option>}
                {orders.map((o) => (
                  <option key={o.order_id} value={o.order_id}>
                    {o.order_id.slice(-20)} · {o.charge_currency || "INR"} {o.charge_amount || o.amount_inr} · {o.status} · {o.email}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-[11px] font-mono uppercase tracking-widest text-muted">Tamper mode</span>
              <select
                className="brutal-input mt-1 w-full"
                value={tamper}
                onChange={(e) => setTamper(e.target.value)}
                data-testid="webhook-test-tamper-select"
              >
                {TAMPERS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </label>
          </div>
          <button
            onClick={fireTest}
            disabled={busy || !selectedOrder}
            className="btn-brutal text-sm"
            data-testid="webhook-test-fire"
          >
            {busy ? "Firing…" : "Send test webhook"}
          </button>
          {lastTest && (
            <div className={`brutal-card p-3 ${RESULT_STYLE[lastTest.response_body?.includes("ok") ? "accepted" : "rejected_signature"] || ""}`} data-testid="webhook-test-result">
              <div className="text-[10px] font-mono uppercase tracking-widest opacity-80">
                Last test · HTTP {lastTest.status_code} · {lastTest.tamper ? `tampered=${lastTest.tamper}` : "valid"}
              </div>
              <div className="text-xs font-mono mt-1 break-words">{lastTest.response_body}</div>
            </div>
          )}
        </section>

        <section className="space-y-3" data-testid="webhook-logs-section">
          <div className="flex items-end justify-between gap-3 flex-wrap">
            <div>
              <h2 className="font-display text-lg font-bold">Recent arrivals</h2>
              <p className="text-xs text-muted mt-1">{logs.length} entries · sorted newest first</p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={resultFilter}
                onChange={(e) => setResultFilter(e.target.value)}
                className="brutal-input text-xs"
                data-testid="webhook-logs-filter"
              >
                <option value="">All results</option>
                <option value="accepted">Accepted</option>
                <option value="rejected_signature">Rejected · signature</option>
                <option value="rejected_replay">Rejected · replay</option>
                <option value="amount_mismatch">Amount mismatch</option>
                <option value="currency_mismatch">Currency mismatch</option>
                <option value="order_not_found">Order not found</option>
              </select>
              <button onClick={() => setRefreshKey((k) => k + 1)} className="btn-ghost text-xs" data-testid="webhook-logs-refresh">Refresh</button>
            </div>
          </div>

          {loading ? (
            <div className="text-muted text-sm font-mono">Loading…</div>
          ) : logs.length === 0 ? (
            <div className="brutal-card p-6 text-center text-muted text-sm" data-testid="webhook-logs-empty">
              No webhook arrivals yet. Use the test tool above to send a signed payload, or register the webhook URL in Cashfree to start receiving real ones.
            </div>
          ) : (
            <ul className="space-y-2" data-testid="webhook-logs-list">
              {logs.map((log) => {
                const styleClass = RESULT_STYLE[log.result] || "border-white/10";
                return (
                  <li key={log.event_id} className={`brutal-card p-3 sm:p-4 ${styleClass}`} data-testid={`webhook-log-${log.event_id}`}>
                    <div className="flex items-center justify-between gap-2 flex-wrap text-xs font-mono">
                      <div className="flex items-center gap-2">
                        <span className="uppercase tracking-widest">{log.result || "unknown"}</span>
                        {log.event_type && <span className="opacity-70">· {log.event_type}</span>}
                      </div>
                      <span className="opacity-60">{new Date(log.received_at).toLocaleString()}</span>
                    </div>
                    {log.order_id && (
                      <div className="text-[11px] font-mono mt-1 opacity-80">order: {log.order_id}</div>
                    )}
                    {log.raw_age_sec !== undefined && (
                      <div className="text-[11px] font-mono mt-1 opacity-80">age: {Math.round(log.raw_age_sec)}s</div>
                    )}
                    {log.body_preview && (
                      <div className="text-[11px] font-mono mt-1 opacity-60 break-all max-h-16 overflow-hidden">{log.body_preview}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="brutal-card p-5 space-y-3" data-testid="webhook-setup-instructions">
          <h2 className="font-display text-lg font-bold">Register the webhook in Cashfree</h2>
          <ol className="text-sm text-muted space-y-2 list-decimal list-inside">
            <li>Open <a href="https://merchant.cashfree.com" target="_blank" rel="noreferrer" className="text-amber underline">merchant.cashfree.com</a> (Test mode for sandbox; Production mode for live).</li>
            <li>Developers → <span className="font-mono text-ink">Webhooks</span> → <span className="font-mono text-ink">Add Webhook URL</span>.</li>
            <li>Paste: <span className="font-mono text-xs text-ink bg-bg/60 px-2 py-1 rounded">{window.location.origin}/api/payments/webhook/cashfree</span></li>
            <li>Enable events: <span className="font-mono text-xs text-ink">PAYMENT_SUCCESS_WEBHOOK</span>, <span className="font-mono text-xs text-ink">PAYMENT_FAILED_WEBHOOK</span>, <span className="font-mono text-xs text-ink">PAYMENT_USER_DROPPED_WEBHOOK</span>.</li>
            <li>Save. Real arrivals will start showing up in this log within seconds of the next test transaction.</li>
          </ol>
        </section>

        <Link to="/admin" className="text-xs font-mono uppercase tracking-widest text-muted hover:text-ink transition inline-block" data-testid="webhook-logs-back-admin">← Back to admin</Link>
      </div>
    </div>
  );
}
