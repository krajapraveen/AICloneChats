/**
 * Pricing page — five plans, marketing-only display.
 *
 * 2026-05-11: Cashfree integration removed.
 * 2026-05-12: Easebuzz integration removed.
 * 2026-05-12: Payment Gateway Abstraction Layer wired in. Instamojo is the
 * active provider; the page reads `/api/payments/status` on mount and shows
 * "Payments offline" until the gateway reports `configured:true`. Subscribe
 * and top-up CTAs then call `/api/payments/instamojo/create-order` and
 * redirect the browser to the returned Instamojo checkout URL.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import { useCredits } from "../hooks/useCredits";
import { launchCashfreeCheckout } from "../lib/cashfree";

const PAYMENTS_UNAVAILABLE_MSG = "Payments are temporarily unavailable. New gateway coming soon.";

// Must match the backend whitelist in analytics_revenue.py /
// payments/router.py. Any new source must be added in BOTH places.
const ALLOWED_PRICING_SOURCES = new Set([
  "landing_hero", "landing_pricing", "dashboard_upgrade", "credits_exhausted",
  "clone_limit_reached", "subscription_expired", "profile_manage_subscription",
  "pay_return_retry", "unknown",
]);

// Plan preselection: which plan_id to highlight + scroll to based on
// where the user came from. Each source is a deliberate product decision,
// not an A/B-test guess.
const SOURCE_TO_PRESELECTED_PLAN = {
  // Top-of-funnel — most people convert to Starter first.
  landing_hero: "starter",
  landing_pricing: "starter",
  // Mid-funnel — user already in the product, ready for the workhorse plan.
  dashboard_upgrade: "pro",
  // Friction signal — they exhausted credits, push the next-bigger tier so
  // they don't bounce back in 30 days.
  credits_exhausted: "pro",
  // Power-user signal — clone limit hit means they're building a lot, send
  // them straight to the creator tier.
  clone_limit_reached: "ultimate",
  // They had a plan and it ran out — preselect the SAME tier they were on
  // (handled dynamically below by reading user.plan_id when known), default
  // to Pro otherwise.
  subscription_expired: "pro",
  // From inside the Profile flow — they're managing, default to Pro.
  profile_manage_subscription: "pro",
  // From a failed checkout retry — keep them on Pro (the most common attempt).
  pay_return_retry: "pro",
  unknown: null,  // No preselection — show neutral pricing page
};

function planTone(tier) {
  return [
    { border: "border-white/10", accent: "text-muted", label: "FREE" },
    { border: "border-amber/30", accent: "text-amber", label: "STARTER" },
    { border: "border-violet/40", accent: "text-violet-soft", label: "PRO" },
    { border: "border-emerald/40", accent: "text-emerald-300", label: "PREMIUM" },
    { border: "border-rose/40", accent: "text-rose-300", label: "ULTIMATE" },
  ][tier] || { border: "border-white/10", accent: "text-muted", label: "" };
}

// Soft human label for the banner. Source names are machine-friendly; we
// translate them to one short sentence so the user knows we noticed.
const SOURCE_BANNER_COPY = {
  credits_exhausted: "You ran out of credits — we're showing the plan that fits your usage.",
  clone_limit_reached: "You hit your clone limit — Ultimate gives you the most room to build.",
  subscription_expired: "Your previous plan expired. Pick up where you left off.",
  dashboard_upgrade: "Upgrade your plan to unlock more credits and features.",
  pay_return_retry: "Let's try that again. Same plan, fresh checkout.",
  profile_manage_subscription: "Manage your plan below.",
  landing_hero: null,
  landing_pricing: null,
  unknown: null,
};

export default function Pricing() {
  const { user } = useAuth();
  const credits = useCredits();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [plans, setPlans] = useState([]);
  const [costs, setCosts] = useState({});
  const [catalog, setCatalog] = useState(null);
  const [topups, setTopups] = useState({ packs: [], country_code: "", is_active_subscriber: false });
  const [gateway, setGateway] = useState({ configured: false, provider: "", env: "", display_name: "" });
  const [busyId, setBusyId] = useState(null);
  const cardRefs = useRef({});

  // Read pricing_visit_source from URL once; ignore non-whitelisted values.
  const visitSource = useMemo(() => {
    const raw = (searchParams.get("source") || "").toLowerCase();
    return ALLOWED_PRICING_SOURCES.has(raw) ? raw : "unknown";
  }, [searchParams]);

  const visitIntent = useMemo(() => searchParams.get("intent") || null, [searchParams]);

  // Plan preselection: prefer the user's previous plan when expired, otherwise
  // use the static source→plan table. Never preselect on "unknown" — neutrally
  // show the full catalogue.
  const preselectedPlanId = useMemo(() => {
    if (visitSource === "subscription_expired" && credits?.plan_id && credits.plan_id !== "free") {
      return credits.plan_id;
    }
    return SOURCE_TO_PRESELECTED_PLAN[visitSource] || null;
  }, [visitSource, credits?.plan_id]);

  useEffect(() => {
    let cancelled = false;
    // Pass source + referrer through to funnel ingestion for attribution.
    api.post("/funnel/event", {
      event_name: "pricing_view",
      referrer: document.referrer || null,
      source: visitSource,
      intent: visitIntent,
    }).catch(() => {});
    (async () => {
      try {
        const [{ data: plansData }, { data: cat }, { data: tu }, { data: gw }] = await Promise.all([
          api.get("/plans"),
          api.get("/pricing/catalog"),
          api.get("/topups/catalog"),
          api.get("/payments/status"),
        ]);
        if (cancelled) return;
        setPlans(plansData.plans || []);
        setCosts(plansData.credit_costs || {});
        setCatalog(cat || null);
        setTopups(tu || { packs: [], is_active_subscriber: false });
        setGateway(gw || { configured: false });
      } catch (e) {
        toast.error("Could not load plans. Try refresh.");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // After plans load, scroll the preselected card into view smoothly.
  useEffect(() => {
    if (!preselectedPlanId || plans.length === 0) return;
    const el = cardRefs.current[preselectedPlanId];
    if (!el) return;
    // Defer one frame so layout is settled
    const t = setTimeout(() => {
      try { el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_e) { /* ignore */ }
    }, 250);
    return () => clearTimeout(t);
  }, [preselectedPlanId, plans.length]);

  const startCheckout = async ({ planId, packId }) => {
    if (!user) {
      navigate("/login?redirect=/pricing");
      return;
    }
    if (!gateway.configured) {
      toast.error(PAYMENTS_UNAVAILABLE_MSG);
      api.post("/funnel/event", { event_name: "payments_unavailable_click", referrer: planId || packId }).catch(() => {});
      return;
    }
    const id = planId || packId;
    setBusyId(id);
    api.post("/funnel/event", { event_name: "checkout_clicked", referrer: id }).catch(() => {});
    const body = { pricing_visit_source: visitSource };
    if (planId) body.plan_id = planId;
    if (packId) body.pack_id = packId;
    try {
      // Provider-aware create-order URL. Each provider has its own alias path,
      // but the response shape is identical (defined by OrderResponse in
      // backend/payments/base.py). The dispatch below picks the right SDK.
      const aliasPath = gateway.provider === "cashfree"
        ? "/payments/cashfree/create-order"
        : "/payments/instamojo/create-order";
      const { data } = await api.post(aliasPath, body);
      const providerName = data?.provider || gateway.provider;
      const payload = data?.payload || {};

      if (providerName === "cashfree" && payload.payment_session_id) {
        // Cashfree v3: launch their JS SDK; SDK redirects browser to return_url
        const res = await launchCashfreeCheckout({
          paymentSessionId: payload.payment_session_id,
          mode: payload.mode || data?.env || "production",
        });
        if (!res.started) {
          toast.error(res.error || "Could not start checkout.");
          setBusyId(null);
        }
        // If started, Cashfree owns the screen until it redirects back.
        return;
      }

      if (data?.checkout_url) {
        // Instamojo + any other redirect-style gateway
        window.location.assign(data.checkout_url);
        return;
      }

      toast.error("Gateway did not return a usable checkout token. Please try again.");
      setBusyId(null);
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const msg = (typeof detail === "object" ? detail?.message : detail) || e?.message || "Could not start checkout.";
      toast.error(msg);
      setBusyId(null);
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
          {!gateway.configured && (
            <div className="brutal-card p-4 border-amber/40 bg-amber-500/10" data-testid="pricing-payments-unavailable-banner">
              <div className="text-amber font-mono text-[11px] uppercase tracking-widest mb-1">Payments offline</div>
              <div className="text-sm">We&apos;re finishing the new gateway setup. Subscribe and top-up buttons are temporarily inert. The catalog below stays accurate — your existing plan and credit balance are unaffected.</div>
            </div>
          )}
          {gateway.configured && gateway.env === "test" && (
            <div className="brutal-card p-3 border-amber/40 bg-amber-500/5" data-testid="pricing-test-mode-banner">
              <div className="text-amber font-mono text-[10px] uppercase tracking-widest">Test mode · {gateway.display_name || gateway.provider} sandbox</div>
              <div className="text-xs text-muted mt-1">Use sandbox test card / UPI details. No real money is charged.</div>
            </div>
          )}
          {SOURCE_BANNER_COPY[visitSource] && (
            <div className="brutal-card p-3 border-violet/40 bg-violet-500/5" data-testid="pricing-source-banner">
              <div className="text-violet-soft font-mono text-[10px] uppercase tracking-widest mb-0.5">
                {visitSource.replaceAll("_", " ")}
              </div>
              <div className="text-sm text-ink/90">{SOURCE_BANNER_COPY[visitSource]}</div>
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
            const isPreselected = preselectedPlanId === p.plan_id;
            return (
              <article
                key={p.plan_id}
                ref={(el) => { if (el) cardRefs.current[p.plan_id] = el; }}
                className={`brutal-card p-5 flex flex-col gap-3 ${tone.border} ${isPreselected ? "ring-2 ring-amber/60 shadow-[0_0_24px_rgba(245,158,11,0.18)] relative" : ""}`}
                data-testid={`pricing-card-${p.plan_id}`}
                data-preselected={isPreselected ? "true" : "false"}
              >
                {isPreselected && (
                  <div className="absolute -top-2 left-3 px-2 py-0.5 rounded-md bg-amber text-black text-[9px] font-mono uppercase tracking-widest font-bold" data-testid={`preselected-badge-${p.plan_id}`}>
                    Recommended for you
                  </div>
                )}
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
                    title={!gateway.configured ? PAYMENTS_UNAVAILABLE_MSG : `Subscribe to ${p.name}`}
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
                    title={locked ? "Subscribe to unlock top-ups" : (!gateway.configured ? PAYMENTS_UNAVAILABLE_MSG : `Buy ${pack.name}`)}
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
          {gateway.configured ? `Payments by ${gateway.display_name || gateway.provider} · ` : ""}Secure server-side payment verification · No card data touches our servers
        </footer>
      </div>
    </div>
  );
}
