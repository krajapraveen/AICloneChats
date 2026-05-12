import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { buildUpgradeUrl } from "../lib/upgrade";

/**
 * GlobalPaywallModal — listens for the `paywall:hit` window event emitted by
 * the axios interceptor on every 402 from a monetized backend surface, and
 * renders a hard, friction-free modal that funnels the user to /pricing.
 *
 * Constitutional rules:
 * - No streaks, no nudges, no badges. Just facts: what's locked, why, and
 *   the upgrade path. Dismissable in a single tap.
 * - Server is the source of truth — the codes here are display-only.
 */
const COPY_BY_CODE = {
  subscription_required: {
    title: "Subscribe to unlock this feature",
    body: "New accounts start at 0 credits. Pick a plan to start chatting.",
    cta: "See plans",
  },
  plan_upgrade_required: {
    title: "Upgrade your plan",
    body: "This feature is on a higher tier. Upgrade to keep going.",
    cta: "See plans",
  },
  insufficient_balance: {
    title: "You're out of credits",
    body: "Top up or upgrade to keep chatting. Active subscribers can also buy a top-up pack.",
    cta: "Top up · Upgrade",
  },
  daily_cap_reached: {
    title: "Daily limit reached",
    body: "You've hit today's cap on this plan. It resets every 24 hours.",
    cta: "See plans",
  },
  email_not_verified: {
    title: "Subscribe to unlock this feature",
    body: "Pick a plan to start chatting.",
    cta: "See plans",
  },
  subscription_required_for_topup: {
    title: "Top-ups are for subscribers",
    body: "Top-up packs are reserved for active subscribers. Subscribe to a plan first.",
    cta: "See plans",
  },
  auth_required: {
    title: "Sign in to continue",
    body: "This feature requires an account.",
    cta: "Sign in",
  },
  fraud_cooldown: {
    title: "Activity paused briefly",
    body: "We've paused activity on this account briefly. Try again in a few hours.",
    cta: "Got it",
  },
};

export default function GlobalPaywallModal() {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    const handler = (ev) => {
      const d = ev?.detail || {};
      setDetail(d);
      setOpen(true);
    };
    window.addEventListener("paywall:hit", handler);
    return () => window.removeEventListener("paywall:hit", handler);
  }, []);

  if (!open || !detail) return null;

  const code = detail.code || "subscription_required";
  const copy = COPY_BY_CODE[code] || COPY_BY_CODE.subscription_required;

  const onPrimary = () => {
    setOpen(false);
    if (code === "auth_required") {
      navigate("/login");
    } else if (code === "fraud_cooldown") {
      // dismiss only
    } else {
      // Every other code (subscription_required, plan_upgrade_required,
      // insufficient_balance, daily_cap_reached, email_not_verified,
      // subscription_required_for_topup, and any future code) funnels to
      // /pricing through the centralized helper. The verify-email route
      // remains available for users who want it but is no longer the
      // CTA target — verify-gate is off in the current revenue config.
      navigate(buildUpgradeUrl({ source: detail.surface || "paywall_modal" }));
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center px-4"
      role="dialog"
      aria-modal="true"
      data-testid="global-paywall-modal"
    >
      <div className="absolute inset-0 bg-black/75 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="relative max-w-md w-full glass-card p-6 sm:p-7 border-amber/40" data-testid="paywall-modal-body">
        <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber mb-2" data-testid="paywall-code">
          {code.replace(/_/g, " ")}
        </div>
        <h2 className="heading-display text-2xl sm:text-3xl mb-3" data-testid="paywall-title">{copy.title}</h2>
        <p className="text-sm text-muted leading-relaxed mb-4" data-testid="paywall-body">{copy.body}</p>
        {detail.surface && (
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-4">
            Feature: <span className="text-ink">{String(detail.surface).replace(/_/g, " ")}</span>
            {detail.required_plan && <> · Required: <span className="text-ink">{detail.required_plan}</span></>}
            {typeof detail.cost === "number" && <> · Cost: <span className="text-ink">{detail.cost} cr</span></>}
            {typeof detail.credits_balance === "number" && <> · Balance: <span className="text-ink">{detail.credits_balance}</span></>}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={onPrimary}
            className="btn-brutal text-xs flex-1"
            data-testid="paywall-primary-btn"
          >
            {copy.cta}
          </button>
          <button
            onClick={() => setOpen(false)}
            className="btn-ghost text-xs"
            data-testid="paywall-dismiss-btn"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
