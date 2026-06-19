/**
 * "Continue with Apple" — official-styled pill button.
 *
 * Apple Sign-in for web only works on domains you've VERIFIED in your Apple
 * Developer account. We deliberately gate visibility on two conditions:
 *   1. backend reports configured=true (env vars present)
 *   2. hostname matches one of the configured production hosts
 * On preview/local domains the button is hidden so testers never see a
 * guaranteed-to-fail flow.
 *
 * The full OAuth dance happens server-side (FastAPI), so this component does
 * exactly one thing on click: navigate the top-level browser to
 * `/api/auth/apple/login?next=<destination>`. The backend redirects to Apple,
 * Apple form-posts back to `/api/auth/apple/callback`, the backend sets the
 * `session_token` cookie, and finally redirects to the SPA's `<next>` route.
 */
import { useEffect, useState } from "react";
import api from "../lib/api";

// Domains where Sign in with Apple is registered + verified. Anyone shipping
// to another domain (staging.aiclonechats.com etc) just appends here.
// Optional dev override: window.__FORCE_APPLE_BUTTON__ = true to preview the
// rendered button on a non-production host. Auth flow still won't work on
// preview because Apple rejects unregistered redirect URIs.
const PRODUCTION_HOSTS = new Set([
  "aiclonechats.com",
  "www.aiclonechats.com",
]);

const AppleIcon = ({ className = "" }) => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 384 512"
    aria-hidden="true"
    focusable="false"
    className={className}
  >
    <path
      fill="currentColor"
      d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.9 48.6-.7 90.4-82.5 102.6-119.3-65.2-30.7-61.7-90-61.7-91.9zm-56.6-164.2c27.3-32.4 24.8-61.9 24-72.5-24.1 1.4-52 16.4-67.9 34.9-17.5 19.8-27.8 44.3-25.6 71.9 26.1 2 49.9-11.4 69.5-34.3z"
    />
  </svg>
);

export default function AppleSignInButton({
  label = "Continue with Apple",
  testId = "apple-signin-btn",
  next,
}) {
  const [configured, setConfigured] = useState(false);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get("/auth/apple/config")
      .then((r) => {
        if (cancelled) return;
        setConfigured(!!r.data?.configured);
      })
      .catch(() => {
        if (!cancelled) setConfigured(false);
      })
      .finally(() => {
        if (!cancelled) setResolved(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Hide the button entirely on non-production hosts even if env vars are set.
  // Apple's authorize endpoint rejects un-registered redirect URIs, so the
  // flow would just show a 400 page from Apple — bad UX.
  const hostname = typeof window !== "undefined" ? window.location.hostname : "";
  const forceShow = typeof window !== "undefined" && window.__FORCE_APPLE_BUTTON__ === true;
  const isProductionHost = forceShow || PRODUCTION_HOSTS.has(hostname);

  if (!resolved) return null;
  if (!configured) return null;
  if (!isProductionHost) return null;

  const params = new URLSearchParams();
  if (next) params.set("next", next);
  const qs = params.toString();
  const href = `${process.env.REACT_APP_BACKEND_URL || ""}/api/auth/apple/login${qs ? `?${qs}` : ""}`;

  return (
    <a
      href={href}
      data-testid={testId}
      className="w-full mb-5 inline-flex items-center justify-center gap-2 h-11 rounded-full bg-black text-white border border-white/10 hover:bg-neutral-900 transition-colors font-semibold text-sm"
      aria-label={label}
    >
      <AppleIcon />
      <span>{label}</span>
    </a>
  );
}
