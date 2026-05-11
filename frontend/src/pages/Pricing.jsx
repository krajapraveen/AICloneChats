/**
 * Pricing page — five plans, server-authored Cashfree checkout.
 *
 * Frontend NEVER passes amount, plan price, or credit count to the backend.
 * It passes plan_id only. Backend reads PLANS server-side.
 *
 * Cashfree integration uses the official JS SDK in dropIn mode; after
 * checkout completes (success or cancel) the user lands on /pay/return
 * which polls /api/payments/order/:id for the AUTHORITATIVE status.
 * URL-based "success" flags are ignored.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import { useCredits } from "../hooks/useCredits";
import { load as loadCashfree } from "@cashfreepayments/cashfree-js";

function planTone(tier) {
  return [
    { border: "border-white/10", accent: "text-muted", label: "FREE" },
    { border: "border-amber/30", accent: "text-amber", label: "STARTER" },
    { border: "border-violet/40", accent: "text-violet-soft", label: "PRO" },
    { border: "border-emerald/40", accent: "text-emerald-300", label: "PREMIUM" },
    { border: "border-rose/40", accent: "text-rose-300", label: "ULTIMATE" },
  ][tier] || { border: "border-white/10", accent: "text-muted", label: "" };
}

export default function Pricing() {
  const { user } = useAuth();
  const credits = useCredits();
  const navigate = useNavigate();

  const [plans, setPlans] = useState([]);
  const [costs, setCosts] = useState({});
  const [catalog, setCatalog] = useState(null);  // { country_code, currency_code, prices: {plan_id: {...}}, country_source }
  const [topups, setTopups] = useState({ packs: [], country_code: "", is_active_subscriber: false });
  const [busyPlan, setBusyPlan] = useState(null);
  const [busyTopup, setBusyTopup] = useState(null);
  const [cashfreeMode, setCashfreeMode] = useState("test");

  useEffect(() => {
    let cancelled = false;
    // One write per page visit — used by Admin → Revenue → Funnel.
    api.post("/funnel/event", { event_name: "pricing_view", referrer: document.referrer || null }).catch(() => {});
    (async () => {
      try {
        const [{ data: plansData }, { data: cfg }, { data: cat }, { data: tu }] = await Promise.all([
          api.get("/plans"),
          api.get("/payments/config"),
          api.get("/pricing/catalog"),
          api.get("/topups/catalog"),
        ]);
        if (cancelled) return;
        setPlans(plansData.plans || []);
        setCosts(plansData.credit_costs || {});
        setCashfreeMode(cfg?.mode || "test");
        setCatalog(cat || null);
        setTopups(tu || { packs: [], is_active_subscriber: false });
      } catch (e) {
        toast.error("Could not load plans. Try refresh.");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const checkout = async (planId) => {
    if (!user) {
      navigate("/login?redirect=/pricing");
      return;
    }
    setBusyPlan(planId);
    try {
      // 1) Ask backend to author the order. Backend sets amount from PLAN_INDEX.
      const { data: order } = await api.post("/payments/create-order", { plan_id: planId });
      // 2) Hand the session_id to Cashfree Drop-In. SDK opens hosted checkout.
      const cashfree = await loadCashfree({ mode: cashfreeMode });
      await cashfree.checkout({
        paymentSessionId: order.payment_session_id,
        returnUrl: `${window.location.origin}/pay/return?order_id=${order.order_id}`,
        redirectTarget: "_self",
      });
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const msg = typeof detail === "object" ? (detail?.message || detail?.code) : detail;
      toast.error(typeof msg === "string" ? msg : "Could not start checkout.");
    } finally {
      setBusyPlan(null);
    }
  };

  const checkoutTopup = async (packId) => {
    if (!user) {
      navigate("/login?redirect=/pricing");
      return;
    }
    if (!topups.is_active_subscriber) {
      toast.error("Top-up packs are for active subscribers only. Subscribe to a plan first.");
      return;
    }
    setBusyTopup(packId);
    try {
      const { data: order } = await api.post("/payments/create-topup-order", { pack_id: packId });
      const cashfree = await loadCashfree({ mode: cashfreeMode });
      await cashfree.checkout({
        paymentSessionId: order.payment_session_id,
        returnUrl: `${window.location.origin}/pay/return?order_id=${order.order_id}`,
        redirectTarget: "_self",
      });
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const msg = typeof detail === "object" ? detail?.message : detail;
      toast.error(msg || "Could not start top-up checkout.");
    } finally {
      setBusyTopup(null);
    }
  };

  return (
    <div className="min-h-screen page-bg" data-testid="pricing-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-8 py-10 space-y-10">
        <header className="space-y-3 max-w-3xl">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">PRICING</div>
          <h1 className="heading-display text-3xl sm:text-4xl">Premium AI conversations. Built for serious users.</h1>
          <p className="text-sm text-muted">
            Every chat costs credits. Costs are public, server-enforced, and never charged twice.
            New accounts start at 0 credits — subscribe to begin. Refunds happen automatically when the AI fails.
          </p>
          {catalog && (
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted flex items-center gap-2 flex-wrap" data-testid="pricing-locale-banner">
              <span>Detected country: <span className="text-ink">{catalog.country_code}</span></span>
              <span className="opacity-50">·</span>
              <span>Currency: <span className="text-ink">{catalog.currency_code}</span></span>
              {catalog.country_source && (
                <>
                  <span className="opacity-50">·</span>
                  <span>via {catalog.country_source.replace("_", " ")}</span>
                </>
              )}
              <span className="opacity-50">·</span>
              <span className="opacity-80">Prices shown in your local currency based on your detected country.</span>
            </div>
          )}
          {credits.admin_unlimited && (
            <div className="brutal-card p-3 inline-flex items-center gap-2 bg-violet-500/10 border-violet/30" data-testid="pricing-admin-banner">
              <span className="text-xs font-mono uppercase tracking-widest text-violet-soft">Admin · unlimited credits</span>
            </div>
          )}
          {user && !credits.admin_unlimited && (
            <div className="brutal-card p-4 flex items-center justify-between gap-3" data-testid="pricing-balance">
              <div>
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-1">Your balance</div>
                <div className="text-2xl font-display font-bold text-ink">{credits.credits_balance} credits</div>
                <div className="text-xs text-muted mt-1">Plan: {credits.plan_name}{credits.daily_cap ? ` · Daily cap ${credits.daily_used}/${credits.daily_cap}` : ""}</div>
              </div>
            </div>
          )}
        </header>

        <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3 sm:gap-4" data-testid="pricing-plans-grid">
          {plans.map((p) => {
            const tone = planTone(p.tier_rank);
            const isCurrent = credits.plan_id === p.plan_id;
            const localPrice = catalog?.prices?.[p.plan_id];
            // Free plan never has a localized record — show "Free"
            const displayLabel = p.plan_id === "free"
              ? "Free"
              : (localPrice
                  ? new Intl.NumberFormat(undefined, {
                      style: "currency",
                      currency: localPrice.currency_code,
                      maximumFractionDigits: localPrice.display_decimals,
                      minimumFractionDigits: localPrice.display_decimals,
                    }).format(localPrice.display_amount)
                  : `₹${p.price_inr.toLocaleString("en-IN")}`);
            return (
              <article key={p.plan_id} className={`brutal-card p-5 flex flex-col gap-3 ${tone.border}`} data-testid={`pricing-card-${p.plan_id}`}>
                <div>
                  <div className={`text-[10px] font-mono uppercase tracking-[0.18em] ${tone.accent}`}>{tone.label}</div>
                  <h3 className="font-display text-xl font-bold mt-1">{p.name}</h3>
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="font-display text-3xl font-bold" data-testid={`pricing-display-${p.plan_id}`}>
                    {displayLabel}
                  </span>
                  {p.price_inr > 0 && <span className="text-xs text-muted">/ month</span>}
                </div>
                {localPrice?.requires_currency_disclosure && (
                  <div className="text-[10px] font-mono uppercase tracking-widest text-amber/80" data-testid={`pricing-disclosure-${p.plan_id}`}>
                    Charged as {new Intl.NumberFormat(undefined, { style: "currency", currency: localPrice.charge_currency, maximumFractionDigits: 0 }).format(localPrice.charge_amount)}
                  </div>
                )}
                <div className="text-sm font-display font-bold text-ink">
                  {p.monthly_credits.toLocaleString("en-IN")} credits{p.price_inr > 0 ? " / month" : ""}
                </div>
                <ul className="text-xs text-muted space-y-1.5 flex-1">
                  {(p.features || []).map((f, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="text-emerald-300 text-[10px] mt-0.5">●</span>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
                {p.daily_credit_cap && (
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted">
                    Daily cap: {p.daily_credit_cap}
                  </div>
                )}
                {p.plan_id === "free" ? (
                  isCurrent ? (
                    <div className="btn-ghost text-xs text-center cursor-default" data-testid={`pricing-cta-${p.plan_id}`}>Your current plan</div>
                  ) : user ? (
                    <div className="btn-ghost text-xs text-center cursor-default opacity-60" data-testid={`pricing-cta-${p.plan_id}`}>Free tier · no chats</div>
                  ) : (
                    <button
                      onClick={() => navigate("/register?redirect=/pricing")}
                      className="btn-ghost text-xs"
                      data-testid={`pricing-cta-${p.plan_id}`}
                    >
                      Create account
                    </button>
                  )
                ) : isCurrent ? (
                  <div className="btn-ghost text-xs text-center cursor-default" data-testid={`pricing-cta-${p.plan_id}`}>Your current plan</div>
                ) : (
                  <button
                    onClick={() => checkout(p.plan_id)}
                    disabled={busyPlan === p.plan_id}
                    className="btn-brutal text-xs"
                    data-testid={`pricing-cta-${p.plan_id}`}
                  >
                    {busyPlan === p.plan_id ? "Opening checkout…" : `Subscribe · ${displayLabel}`}
                  </button>
                )}
              </article>
            );
          })}
        </section>

        <section className="space-y-4" data-testid="pricing-topup-section">
          <div className="flex items-baseline justify-between flex-wrap gap-2">
            <div>
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">SUBSCRIBER TOP-UPS</div>
              <h2 className="heading-display text-xl">Need more credits this month?</h2>
              <p className="text-sm text-muted max-w-2xl mt-1">
                Top-up packs are available to active subscribers only. They never change your plan or renewal date —
                just add credits to your balance.
              </p>
            </div>
            {!topups.is_active_subscriber && (
              <span className="text-[11px] font-mono uppercase tracking-widest text-amber/80" data-testid="topup-locked-hint">
                {user ? "Subscribe to a plan to unlock top-ups" : "Sign in & subscribe to unlock top-ups"}
              </span>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            {(topups.packs || []).map((pack) => {
              const tp = pack.price;
              const label = tp
                ? new Intl.NumberFormat(undefined, {
                    style: "currency",
                    currency: tp.currency_code,
                    maximumFractionDigits: tp.display_decimals,
                    minimumFractionDigits: tp.display_decimals,
                  }).format(tp.display_amount)
                : `₹${pack.price_inr}`;
              const locked = !topups.is_active_subscriber;
              return (
                <article key={pack.pack_id} className={`brutal-card p-4 flex flex-col gap-2 ${pack.is_popular ? "border-violet/40" : "border-white/10"} ${locked ? "opacity-60" : ""}`} data-testid={`topup-card-${pack.pack_id}`}>
                  <div className="flex items-center justify-between">
                    <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">{pack.name}</div>
                    {pack.is_popular && <span className="tag tag-violet text-[9px]">POPULAR</span>}
                  </div>
                  <div className="flex items-baseline gap-1">
                    <span className="font-display text-2xl font-bold" data-testid={`topup-display-${pack.pack_id}`}>{label}</span>
                  </div>
                  {tp?.requires_currency_disclosure && (
                    <div className="text-[10px] font-mono uppercase tracking-widest text-amber/80">
                      Charged as {new Intl.NumberFormat(undefined, { style: "currency", currency: tp.charge_currency, maximumFractionDigits: 0 }).format(tp.charge_amount)}
                    </div>
                  )}
                  <div className="text-sm font-display font-bold text-ink">{pack.credits.toLocaleString("en-IN")} credits</div>
                  <p className="text-xs text-muted flex-1">{pack.blurb}</p>
                  <button
                    onClick={() => checkoutTopup(pack.pack_id)}
                    disabled={locked || busyTopup === pack.pack_id}
                    className="btn-brutal text-xs"
                    data-testid={`topup-cta-${pack.pack_id}`}
                  >
                    {busyTopup === pack.pack_id ? "Opening checkout…" : locked ? "Subscribers only" : `Buy · ${label}`}
                  </button>
                </article>
              );
            })}
          </div>
        </section>

        <section className="space-y-3" data-testid="pricing-cost-table">
          <h2 className="heading-display text-xl">Credit cost per AI message</h2>
          <p className="text-sm text-muted max-w-2xl">Costs are server-enforced. Tampering the request body does nothing — the backend reads the cost from a code table.</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 sm:gap-3">
            {Object.entries(costs).map(([surface, cost]) => (
              <div key={surface} className="glass-card p-3" data-testid={`pricing-cost-${surface}`}>
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted">{surface.replace(/_/g, " ")}</div>
                <div className="text-base font-display font-bold mt-0.5">{cost} cr</div>
              </div>
            ))}
          </div>
        </section>

        <footer className="pt-6 border-t border-white/5 text-[11px] font-mono uppercase tracking-widest text-muted" data-testid="pricing-footer">
          Payments by Cashfree · Secure server-side webhook verification · No card data touches our servers
        </footer>
      </div>
    </div>
  );
}
