import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export default function AuthCallback() {
  const navigate = useNavigate();
  const { exchangeGoogleSession } = useAuth();
  const processed = useRef(false);

  useEffect(() => {
    // Synchronously guard against StrictMode double-invoke
    if (processed.current) return;
    processed.current = true;

    const hash = window.location.hash || "";
    const m = hash.match(/session_id=([^&]+)/);
    const sessionId = m ? decodeURIComponent(m[1]) : null;

    (async () => {
      if (!sessionId) {
        navigate("/login", { replace: true });
        return;
      }
      try {
        await exchangeGoogleSession(sessionId);
        // Clean URL
        window.history.replaceState({}, "", "/dashboard");
        navigate("/dashboard", { replace: true });
      } catch (e) {
        console.error("OAuth exchange failed", e);
        navigate("/login?error=oauth", { replace: true });
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-cream">
      <div className="brutal-card p-8 text-center">
        <p className="font-display font-extrabold text-2xl">Signing you in…</p>
        <p className="text-sm text-muted-foreground mt-2">Hold tight, building your clone HQ.</p>
      </div>
    </div>
  );
}
