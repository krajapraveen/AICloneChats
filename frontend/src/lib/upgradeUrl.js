/**
 * Pure URL builder for upgrade flows. Zero React imports so it can be unit
 * tested in plain Jest with no DOM/router setup.
 *
 * The destination MUST be /pricing for every chat surface, paywall modal,
 * and future entry point. Historical broken targets (/billing, /upgrade,
 * /plans, /subscribe) must NEVER appear in returned URLs.
 */
export const UPGRADE_DESTINATION = "/pricing";

export function buildUpgradeUrl({ source, intent = "upgrade" } = {}) {
  const params = new URLSearchParams();
  if (source) {
    params.set("source", source);
    if (intent) params.set("intent", intent);
  }
  const qs = params.toString();
  return qs ? `${UPGRADE_DESTINATION}?${qs}` : UPGRADE_DESTINATION;
}
