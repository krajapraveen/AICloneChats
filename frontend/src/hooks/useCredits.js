/**
 * Credit & plan state hook — single source of truth for any component that
 * needs to render the balance / plan / out-of-credits state.
 *
 * Never trust the value in localStorage for any deduction-relevant decision.
 * The backend re-checks credits server-side on every /api/.../generate-style
 * call and will return 402 with a detailed code if the user is out.
 */
import { useCallback, useEffect, useState } from "react";
import api from "../lib/api";

export function useCredits() {
  const [state, setState] = useState({
    loading: true,
    error: null,
    admin_unlimited: false,
    credits_balance: null,
    plan_id: null,
    plan_name: null,
    daily_cap: null,
    daily_used: 0,
    fraud_cooldown_active: false,
    email_verified: false,
    recent_events: [],
  });

  const refresh = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const r = await api.get("/me/credits");
      setState({ loading: false, error: null, ...r.data });
    } catch (e) {
      setState((s) => ({
        ...s,
        loading: false,
        error: e?.response?.data?.detail || "Could not load credits",
      }));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { ...state, refresh };
}
