/**
 * Regression: every Upgrade-to-Pro entry point must funnel to /pricing.
 *
 * Bug history (2026-05-11):
 *   Across iterations, individual chat surfaces (Smart Reply, Voice
 *   Messaging) had hardcoded `navigate("/pricing?source=...")` strings and
 *   the GlobalPaywallModal had a branch for `email_not_verified` that sent
 *   users to /verify-email (now a dead end after the verify-gate was
 *   disabled). The risk is that future surfaces drift to broken targets
 *   like /billing, /upgrade, /plans, /subscribe.
 *
 *   Fix: introduce a single helper `lib/upgrade.js::buildUpgradeUrl` +
 *   `useUpgradeToPro`. Every chat surface and the paywall modal uses it.
 *   This file LOCKS the helper's contract.
 */
import { buildUpgradeUrl, UPGRADE_DESTINATION } from "../upgradeUrl";

describe("buildUpgradeUrl", () => {
  test("base destination is always /pricing", () => {
    expect(UPGRADE_DESTINATION).toBe("/pricing");
  });

  test("with no options returns plain /pricing", () => {
    expect(buildUpgradeUrl()).toBe("/pricing");
    expect(buildUpgradeUrl({})).toBe("/pricing");
  });

  test("encodes source + default intent=upgrade", () => {
    expect(buildUpgradeUrl({ source: "smart_reply" })).toBe(
      "/pricing?source=smart_reply&intent=upgrade"
    );
  });

  test("intent override is respected", () => {
    expect(buildUpgradeUrl({ source: "voice_messaging", intent: "topup" })).toBe(
      "/pricing?source=voice_messaging&intent=topup"
    );
  });

  test("falsy intent becomes empty (still /pricing)", () => {
    expect(buildUpgradeUrl({ source: "x", intent: "" })).toBe(
      "/pricing?source=x"
    );
  });

  // CRITICAL: never resolve to broken targets, ever, for any input
  test.each([
    { source: undefined },
    { source: null },
    { source: "ai_clone" },
    { source: "voice_chat" },
    { source: "anonymous_reality" },
    { source: "debate_rooms" },
    { source: "translation" },
    { source: "delayed_emotional" },
    { source: "mood_chat" },
    { source: "smart_reply" },
    { source: "voice_messaging" },
    { source: "paywall_modal" },
  ])("never produces a non-/pricing path for source=$source", ({ source }) => {
    const url = buildUpgradeUrl({ source });
    const pathname = url.split("?")[0];
    expect(pathname).toBe("/pricing");
    // Negative guard — must NOT contain any of the historically broken paths
    expect(url).not.toMatch(/\/billing\b/);
    expect(url).not.toMatch(/\/upgrade\b/);
    expect(url).not.toMatch(/\/plans\b/);
    expect(url).not.toMatch(/\/subscribe\b/);
  });
});
