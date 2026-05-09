import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useGoogleLogin } from "@react-oauth/google";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";

function GoogleButtonInner({ label, testId, onSuccess, navigate, refresh }) {
  const [loading, setLoading] = useState(false);
  const login = useGoogleLogin({
    flow: "auth-code",
    onSuccess: async (resp) => {
      setLoading(true);
      try {
        // window.location.origin is required — never hardcoded.
        const redirect_uri = window.location.origin;
        const { data } = await api.post("/auth/google/callback", { code: resp.code, redirect_uri });
        if (data.session_token) localStorage.setItem("session_token", data.session_token);
        await refresh();
        if (onSuccess) onSuccess(data); else navigate("/dashboard");
      } catch (err) {
        const detail = err?.response?.data?.detail || "Google sign-in failed";
        toast.error(typeof detail === "string" ? detail : "Google sign-in failed");
      } finally {
        setLoading(false);
      }
    },
    onError: () => toast.error("Google sign-in cancelled or failed"),
  });
  return (
    <button type="button" onClick={() => login()} disabled={loading} className="btn-ghost w-full mb-5" data-testid={testId}>
      <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
        <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.6-6 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34.5 6.5 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z" />
        <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3 0 5.8 1.1 7.9 3l5.7-5.7C34.5 6.5 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
        <path fill="#4CAF50" d="M24 44c5.4 0 10.3-2.1 14-5.4l-6.5-5.5c-2 1.5-4.6 2.4-7.5 2.4-5.3 0-9.7-3.4-11.3-8l-6.5 5C9.6 39.6 16.3 44 24 44z" />
        <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.4 4.3-4.5 5.7l6.5 5.5c4.6-4.2 7.7-10.5 7.7-17.7 0-1.3-.1-2.7-.4-3.5z" />
      </svg>
      {loading ? "Signing in…" : label}
    </button>
  );
}

/**
 * Custom Google Sign-In button.
 * REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
 *
 * If GOOGLE_CLIENT_ID is not configured server-side yet, we render a disabled placeholder
 * so the page does not crash (`useGoogleLogin` requires GoogleOAuthProvider in the tree).
 */
export default function GoogleSignInButton({ label = "Continue with Google", testId = "google-signin-btn", onSuccess }) {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [configured, setConfigured] = useState(null);

  useEffect(() => {
    api.get("/auth/google/config")
      .then((r) => setConfigured(!!r.data?.configured))
      .catch(() => setConfigured(false));
  }, []);

  if (configured === null) {
    return (
      <button type="button" disabled className="btn-ghost w-full mb-5 opacity-50" data-testid={testId}>
        Loading Google…
      </button>
    );
  }
  if (!configured) {
    return (
      <button
        type="button"
        disabled
        className="btn-ghost w-full mb-5 opacity-50"
        data-testid={testId}
        title="Google sign-in is not configured yet"
      >
        Google sign-in unavailable
      </button>
    );
  }

  return <GoogleButtonInner label={label} testId={testId} onSuccess={onSuccess} navigate={navigate} refresh={refresh} />;
}
