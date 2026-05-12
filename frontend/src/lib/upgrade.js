/**
 * Centralized upgrade navigation. Every "Upgrade to Pro" / "Subscribe" /
 * "Top up" CTA across the app MUST go through this helper so future code
 * cannot drift to broken targets (/billing, /upgrade, /plans, /subscribe).
 *
 * Usage in a component:
 *   import { useUpgradeToPro } from "../lib/upgrade";
 *   const upgradeToPro = useUpgradeToPro();
 *   <button onClick={() => upgradeToPro({ source: "smart_reply" })}>
 *     Upgrade to Pro
 *   </button>
 *
 * Contract:
 *   - Destination is ALWAYS /pricing.
 *   - `source` and `intent` are optional analytics breadcrumbs encoded as
 *     query params; they never affect routing.
 *   - Logged-out users still land on /pricing (page is public; checkout
 *     itself bounces them to /login).
 *
 * Pure URL logic lives in ./upgradeUrl.js so it can be unit-tested without
 * a React/router environment. Re-exported here for convenience.
 */
import { useNavigate } from "react-router-dom";

import { buildUpgradeUrl, UPGRADE_DESTINATION } from "./upgradeUrl";

export { buildUpgradeUrl, UPGRADE_DESTINATION };

export function useUpgradeToPro() {
  const navigate = useNavigate();
  return (opts = {}) => {
    navigate(buildUpgradeUrl(opts));
  };
}
