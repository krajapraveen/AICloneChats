/**
 * Cashfree v3 Checkout helper.
 *
 * Flow:
 *  1. Backend create-order returns { provider: "cashfree", payload: { payment_session_id, mode } }
 *  2. We dynamically load https://sdk.cashfree.com/js/v3/cashfree.js (cached)
 *  3. Call cashfree.checkout({ paymentSessionId, redirectTarget: "_self" })
 *
 * Cashfree's SDK handles success/failure/cancel internally and redirects to
 * the `return_url` we configured server-side. Frontend never sees the raw
 * status — the authoritative status is determined by the webhook + the
 * GET /api/payments/order/{id} reconcile on the return page.
 */

const SDK_SRC = "https://sdk.cashfree.com/js/v3/cashfree.js";

let sdkLoadingPromise = null;

function loadCashfreeSdk() {
  if (typeof window === "undefined") return Promise.reject(new Error("no_window"));
  if (window.Cashfree) return Promise.resolve();
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
 * Launch Cashfree's checkout for a paymentSessionId returned by our backend.
 * Returns { started: boolean, error?: string }.
 */
export async function launchCashfreeCheckout({ paymentSessionId, mode = "production" }) {
  if (!paymentSessionId) {
    return { started: false, error: "Missing paymentSessionId from backend." };
  }
  try {
    await loadCashfreeSdk();
  } catch (_e) {
    return { started: false, error: "Could not load the Cashfree checkout. Disable any ad/script blockers and retry." };
  }
  try {
    const cashfree = window.Cashfree({ mode: mode === "production" ? "production" : "sandbox" });
    cashfree.checkout({
      paymentSessionId,
      redirectTarget: "_self",
    });
    return { started: true };
  } catch (e) {
    return { started: false, error: e?.message || "Cashfree checkout failed to launch." };
  }
}

export const __testables = { loadCashfreeSdk, SDK_SRC };
