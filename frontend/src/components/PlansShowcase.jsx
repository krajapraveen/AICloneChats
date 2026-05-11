/**
 * PlansShowcase — top-of-dashboard subscription + top-up cards.
 *
 * Strict rules:
 * - Pricing comes from the backend pricing engine (/api/pricing/catalog).
 *   No frontend math.
 * - Top-up cards visually state "Available only for active subscribers."
 * - Top-up CTA disabled for non-subscribers; click still 403s server-side as
 *   the last line of defense.
 * - No new monetization mechanics: this is visibility only.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";

function priceLabel(price, fallback) {
  if (!price) return fallback || "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: price.currency_code,
      maximumFractionDigits: price.display_decimals,
      minimumFractionDigits: price.display_decimals,
    }).format(price.display_amount);
  } catch {
    return `${price.currency_code} ${price.display_amount}`;
  }
}

export default function PlansShowcase({ user, credits }) {
  const [plans, setPlans] = useState([]);
  const [catalog, setCatalog] = useState(null);
  const [topups, setTopups] = useState({ packs: [], is_active_subscriber: false });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [{ data: p }, { data: c }, { data: t }] = await Promise.all([
          api.get("/plans"),
          api.get("/pricing/catalog"),
          api.get("/topups/catalog"),
        ]);
        if (cancelled) return;
        setPlans((p.plans || []).filter((x) => x.plan_id !== "free"));
        setCatalog(c || null);
        setTopups(t || { packs: [], is_active_subscriber: false });
      } catch {
        // silent — Dashboard already renders without plans if backend errors
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const isSubscriber = topups.is_active_subscriber;
  const adminUnlimited = credits?.admin_unlimited;
  const currentPlan = (user?.plan_id || "free").toLowerCase();

  return (
    <section className="space-y-6 mb-10" data-testid="dashboard-plans-section">
      {/* Subscriptions */}
      <div>
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
          <div>
            <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">PLANS</div>
            <h2 className="heading-display text-2xl sm:text-3xl">Pick a plan to start using AI</h2>
            <p className="text-sm text-muted mt-1 max-w-2xl">
              Every interaction is credit-metered. Pricing reflects your region — set by the backend, not your browser.
            </p>
          </div>
          <Link to="/pricing" className="btn-ghost text-xs whitespace-nowrap" data-testid="plans-see-all">See full pricing →</Link>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
          {loading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="brutal-card p-4 h-44 animate-pulse bg-white/[0.02]" aria-hidden />
            ))
          ) : plans.map((p) => {
            const price = catalog?.prices?.[p.plan_id];
            const isCurrent = currentPlan === p.plan_id;
            const accent = p.plan_id === "ultimate" ? "border-violet/40" : p.plan_id === "premium" ? "border-amber/40" : "border-white/10";
            return (
              <article
                key={p.plan_id}
                className={`brutal-card p-4 flex flex-col gap-2 ${accent} ${isCurrent ? "ring-1 ring-amber/40" : ""}`}
                data-testid={`dashboard-plan-${p.plan_id}`}
              >
                <div className="flex items-center justify-between">
                  <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">{p.name}</div>
                  {isCurrent && <span className="tag tag-amber text-[9px]" data-testid={`dashboard-plan-current-${p.plan_id}`}>CURRENT</span>}
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="font-display text-2xl font-bold" data-testid={`dashboard-plan-price-${p.plan_id}`}>{priceLabel(price, `₹${p.price_inr}`)}</span>
                  <span className="text-xs text-muted">/ mo</span>
                </div>
                {price?.requires_currency_disclosure && (
                  <div className="text-[10px] font-mono uppercase tracking-widest text-amber/80">
                    Charged as {new Intl.NumberFormat(undefined, { style: "currency", currency: price.charge_currency, maximumFractionDigits: 0 }).format(price.charge_amount)}
                  </div>
                )}
                <div className="text-sm font-display font-bold text-ink">{(p.monthly_credits ?? 0).toLocaleString("en-IN")} credits / mo</div>
                <p className="text-xs text-muted flex-1">{p.tagline || ""}</p>
                {isCurrent ? (
                  <div className="btn-ghost text-xs text-center cursor-default" data-testid={`dashboard-plan-cta-${p.plan_id}`}>Your current plan</div>
                ) : (
                  <Link to="/pricing" className="btn-brutal text-xs" data-testid={`dashboard-plan-cta-${p.plan_id}`}>
                    {currentPlan === "free" ? "Subscribe" : "Switch plan"}
                  </Link>
                )}
              </article>
            );
          })}
        </div>
      </div>

      {/* Top-up packs */}
      <div data-testid="dashboard-topups-section">
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
          <div>
            <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">TOP-UP PACKS</div>
            <h3 className="heading-display text-xl">Need more credits this month?</h3>
            <p className="text-xs sm:text-sm text-muted mt-1" data-testid="dashboard-topups-locked-note">
              Available only for active subscribers. Top-ups never change your plan or renewal date — they only add credits.
            </p>
          </div>
          {!isSubscriber && !adminUnlimited && (
            <span className="text-[11px] font-mono uppercase tracking-widest text-amber/80" data-testid="dashboard-topup-locked-hint">
              Subscribe to unlock top-ups
            </span>
          )}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {(topups.packs || []).map((pack) => {
            const tp = pack.price;
            const label = priceLabel(tp, `₹${pack.price_inr}`);
            const locked = !isSubscriber && !adminUnlimited;
            return (
              <article
                key={pack.pack_id}
                className={`brutal-card p-3 sm:p-4 flex flex-col gap-1.5 border-white/10 ${locked ? "opacity-70" : ""}`}
                data-testid={`dashboard-topup-${pack.pack_id}`}
              >
                <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-violet-soft">{pack.name}</div>
                <div className="font-display text-xl font-bold" data-testid={`dashboard-topup-price-${pack.pack_id}`}>{label}</div>
                <div className="text-sm font-display font-bold text-ink">{pack.credits.toLocaleString("en-IN")} credits</div>
                <p className="text-[11px] text-muted flex-1">{pack.blurb}</p>
                <Link
                  to="/pricing"
                  onClick={(e) => {
                    if (locked) {
                      e.preventDefault();
                      toast.error("Top-ups are for active subscribers. Subscribe to a plan first.");
                    }
                  }}
                  className="btn-brutal text-[11px] text-center"
                  data-testid={`dashboard-topup-cta-${pack.pack_id}`}
                >
                  {locked ? "Subscribers only" : "Buy on Pricing"}
                </Link>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
