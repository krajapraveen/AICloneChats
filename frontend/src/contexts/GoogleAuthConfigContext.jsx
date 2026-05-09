import { createContext, useContext, useEffect, useState } from "react";
import api from "../lib/api";

/**
 * Exposes Google OAuth config (configured / clientId) loaded from /api/auth/google/config.
 * Children mount only AFTER config is resolved, so any descendant using @react-oauth/google
 * is guaranteed to have a GoogleOAuthProvider ancestor (or know it isn't there).
 *
 * REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
 */
const GoogleAuthConfigContext = createContext({ configured: false, clientId: "", ready: false });

export function GoogleAuthConfigProvider({ children, fallback = null }) {
  const [state, setState] = useState({ configured: false, clientId: "", ready: false });

  useEffect(() => {
    api.get("/auth/google/config")
      .then((r) => setState({
        configured: !!r.data?.configured,
        clientId: r.data?.client_id || "",
        ready: true,
      }))
      .catch(() => setState({ configured: false, clientId: "", ready: true }));
  }, []);

  if (!state.ready) return fallback;

  return (
    <GoogleAuthConfigContext.Provider value={state}>
      {children}
    </GoogleAuthConfigContext.Provider>
  );
}

export function useGoogleAuthConfig() {
  return useContext(GoogleAuthConfigContext);
}
