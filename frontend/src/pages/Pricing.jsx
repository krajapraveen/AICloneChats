/**
 * Pricing page — five plans + subscriber top-ups.
 *
 * 2026-05-11: Cashfree removed.
 * 2026-05-12: Easebuzz wired in (test mode). Subscribe + top-up CTAs now call
 * /api/payments/easebuzz/create-order and hand off to the Easebuzz Checkout
 * SDK via lib/easebuzz.js.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import { useCredits } from "../hooks/useCredits";
import { startEasebuzzCheckout } from "../lib/easebuzz";

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
  const [catalog, setCatalog] = useState(null);
  const [topups, setTopups] = useState({ packs: [], country_code: "", is_active_subscriber: false });
  const [gateway, setGateway] = useState({ configured: false, env: "test" });
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.post("/funnel/event", { event_name: "pricing_view", referrer: document.referrer || null }).catch(() => {});
    (async () => {
      try {
        const [{ data: plansData }, { data: cat }, { data: tu }, { data: gw }] = await Promise.all([
          api.get("/plans"),
          api.get("/pricing/catalog"),
          api.get("/topups/catalog"),
          api.get("/payments/easebuzz/config"),
        ]);
        if (cancelled) return;
        setPlans(plansData.plans || []);
        setCosts(plansData.credit_costs || {});
        setCatalog(cat || null);
        setTopups(tu || { packs: [], is_active_subscriber: false });
        setGateway(gw || { configured: false, env: "test" });
      } catch (e) {
        toast.error("Could not load plans. Try refresh.");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const startCheckout = async ({ planId, packId }) => {
    if (!user) {
      navigate("/login?redirect=/pricing");
      return;
    }
    if (!gateway.configured) {
      toast.error("Payments are temporarily unavailable. New gateway coming soon.");
      return;
    }
    const id = planId || packId;
    setBusyId(id);
    api.post("/funnel/event", { event_name: "checkout_clicked", referrer: id }).catch(() => {});
    const res = await startEasebuzzCheckout({ planId, packId });
    if (!res.started) {
      toast.error(res.error || "Could not start checkout.");
      setBusyId(null);
      return;
    }
    // SDK overlay is now controlling the screen — keep busy state until the
    // user closes it (onResponse navigates to /pay/return).
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
          {!gateway.configured && (
            <div className="brutal-card p-4 border-amber/40 bg-amber-500/10" data-testid="pricing-payments-unavailable-banner">
              <div className="text-amber font-mono text-[11px] uppercase tracking-widest mb-1">Payments offline</div>
              <div className="text-sm">Payments are temporarily unavailable while we finish configuring our new gateway. Plans below stay accurate — your existing plan and credit balance are unaffected.</div>
            </div>
          )}
          {gateway.configured && gateway.env === "test" && (
            <div className="brutal-card p-3 border-amber/40 bg-amber-500/5" data-testid="pricing-test-mode-banner">
              <div className="text-amber font-mono text-[10px] uppercase tracking-widest">Test mode · Easebuzz sandbox</div>
              <div className="text-xs text-muted mt-1">Use Easebuzz test card details. No real money is charged.</div>
            </div>
          )}
        </header>

        <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3 sm:gap-4" data-testid="pricing-plans-grid">
          {plans.map((p) => {
            const tone = planTone(p.tier_rank);
            const isCurrent = credits.plan_id === p.plan_id;
            const localPrice = catalog?.prices?.[p.plan_id];
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
            const isBusy = busyId === p.plan_id;
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
                    onClick={() => startCheckout({ planId: p.plan_id })}
                    disabled={isBusy || !gateway.configured}
                    className={`btn-brutal text-xs ${(!gateway.configured) ? "opacity-50 cursor-not-allowed" : ""}`}
                    data-testid={`pricing-cta-${p.plan_id}`}
                    title={!gateway.configured ? "Payments are temporarily unavailable." : `Subscribe to ${p.name}`}
                  >
                    {isBusy ? "Opening checkout…" : (gateway.configured ? `Subscribe · ${displayLabel}` : `Coming soon · ${displayLabel}`)}
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
              const isBusy = busyId === pack.pack_id;
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
                    onClick={() => startCheckout({ packId: pack.pack_id })}
                    disabled={locked || isBusy || !gateway.configured}
                    className={`btn-brutal text-xs ${(locked || !gateway.configured) ? "opacity-50 cursor-not-allowed" : ""}`}
                    data-testid={`topup-cta-${pack.pack_id}`}
                    title={locked ? "Subscribe to unlock top-ups" : (!gateway.configured ? "Payments temporarily unavailable" : `Buy ${pack.name}`)}
                  >
                    {locked ? "Subscribers only" : (isBusy ? "Opening checkout…" : (gateway.configured ? `Buy · ${label}` : `Coming soon · ${label}`))}
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
          Payments by Easebuzz · Secure server-side webhook verification · No card data touches our servers
        </footer>
      </div>
    </div>
  );
}
