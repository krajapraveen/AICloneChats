/**
 * Easebuzz checkout integration helper.
 *
 * Flow:
 *  1. POST /api/payments/easebuzz/create-order  → backend returns access_key + env + key
 *  2. Load Easebuzz Checkout JS SDK from CDN (cached after first load)
 *  3. new EasebuzzCheckout(key, env).initiatePayment({ access_key, onResponse, theme })
 *  4. On any response, push the user to /pay/return?order_id=... so the
 *     authoritative server-side reconcile decides what they see.
 *
 * If the SDK cannot load (CSP / network / ad-blocker), we fall back to a
 * full-page redirect to the hosted checkout URL the backend already returned.
 */
import api from "./api";

const SDK_SRC = "https://ebz-static.s3.ap-south-1.amazonaws.com/easecheckout/v2.0.0/easebuzz-checkout.js";

let sdkLoadingPromise = null;

function loadEasebuzzSdk() {
  if (typeof window === "undefined") return Promise.reject(new Error("no_window"));
  if (window.EasebuzzCheckout) return Promise.resolve();
  if (sdkLoadingPromise) return sdkLoadingPromise;
  sdkLoadingPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${SDK_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("sdk_load_failed")));
      return;
    }
    const s = document.createElement("script");
    s.src = SDK_SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      sdkLoadingPromise = null;
      reject(new Error("sdk_load_failed"));
    };
    document.body.appendChild(s);
  });
  return sdkLoadingPromise;
}

/**
 * Start the Easebuzz checkout for either a plan (subscription) or a top-up pack.
 *
 * @param {{ planId?: string, packId?: string, onReturn?: (orderId:string)=>void }} opts
 * @returns {Promise<{started: boolean, error?: string, orderId?: string}>}
 */
export async function startEasebuzzCheckout({ planId, packId, onReturn }) {
  const body = {};
  if (planId) body.plan_id = planId;
  if (packId) body.pack_id = packId;

  let order;
  try {
    const { data } = await api.post("/payments/easebuzz/create-order", body);
    order = data;
  } catch (e) {
    const detail = e?.response?.data?.detail;
    const code = typeof detail === "object" ? detail?.code : null;
    const message =
      (typeof detail === "object" ? detail?.message : detail) ||
      e?.message ||
      "Could not start checkout.";
    return { started: false, error: message, code };
  }

  const { access_key: accessKey, key, env, order_id: orderId, hosted_url: hostedUrl } = order || {};
  if (!accessKey || !key) {
    return { started: false, error: "Gateway did not return a checkout token.", orderId };
  }

  const goReturn = () => {
    if (onReturn) onReturn(orderId);
    else if (typeof window !== "undefined") {
      window.location.assign(`/pay/return?order_id=${encodeURIComponent(orderId)}`);
    }
  };

  try {
    await loadEasebuzzSdk();
  } catch (_e) {
    // SDK failed — fall back to hosted page
    if (hostedUrl && typeof window !== "undefined") {
      window.location.assign(hostedUrl);
      return { started: true, orderId };
    }
    return { started: false, error: "Checkout could not load. Please try again.", orderId };
  }

  // eslint-disable-next-line new-cap
  const checkout = new window.EasebuzzCheckout(key, env || "test");
  checkout.initiatePayment({
    access_key: accessKey,
    onResponse: () => {
      // We do NOT trust the client-side status. The webhook + reconcile decide.
      goReturn();
    },
    theme: "#7c3aed",
  });
  return { started: true, orderId };
}

export const __testables = { loadEasebuzzSdk, SDK_SRC };
