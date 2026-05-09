import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api from "../lib/api";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Skip /me check on truly public routes if there's no token to avoid noisy 401s
    const hasToken = typeof window !== "undefined" && !!localStorage.getItem("session_token");
    if (!hasToken) {
      setLoading(false);
      return;
    }
    checkAuth();
  }, [checkAuth]);

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data.session_token) localStorage.setItem("session_token", data.session_token);
    setUser(data.user);
    setLoading(false);
    return data.user;
  };

  const register = async (email, password, name) => {
    const { data } = await api.post("/auth/register", { email, password, name });
    if (data.session_token) localStorage.setItem("session_token", data.session_token);
    setUser(data.user);
    setLoading(false);
    return data.user;
  };

  const loginWithGoogle = async (code, redirect_uri) => {
    const { data } = await api.post("/auth/google/callback", { code, redirect_uri });
    if (data.session_token) localStorage.setItem("session_token", data.session_token);
    setUser(data.user);
    setLoading(false);
    return data.user;
  };

  const logout = async () => {
    try { await api.post("/auth/logout"); } catch {}
    localStorage.removeItem("session_token");
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, loginWithGoogle, refresh: checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
};
