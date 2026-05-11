import axios from "axios";
import { getDeviceId } from "./deviceId";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

const api = axios.create({
  baseURL: API,
  // NOTE: Do NOT use withCredentials. Some hosting layers force
  // Access-Control-Allow-Origin:* which browsers reject when combined
  // with credentials. We auth via Bearer header (localStorage) instead.
  headers: { "Content-Type": "application/json" },
});

// Always attach token from localStorage as Bearer header.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("session_token");
  config.headers = config.headers || {};
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // Stable device id for anonymous trials (Voice Messaging)
  try {
    config.headers["X-Device-Id"] = getDeviceId();
  } catch {
    // ignore
  }
  return config;
});

// Global paywall hook: when any monetized API returns 402 with a structured
// detail, emit a window event so a top-level <GlobalPaywallModal /> can
// surface it. Routes that already manage their own paywall (smart_reply,
// voice) can opt-out by adding config.suppressPaywall=true.
api.interceptors.response.use(
  (r) => r,
  (error) => {
    const status = error?.response?.status;
    const detail = error?.response?.data?.detail;
    const cfg = error?.config || {};
    if ((status === 402 || (status === 403 && typeof detail === "object" && detail?.code === "subscription_required_for_topup")) && typeof detail === "object" && !cfg.suppressPaywall) {
      try {
        window.dispatchEvent(new CustomEvent("paywall:hit", { detail }));
      } catch {
        // ignore
      }
    }
    return Promise.reject(error);
  }
);

export default api;
