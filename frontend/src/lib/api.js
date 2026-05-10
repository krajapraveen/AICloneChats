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

export default api;
