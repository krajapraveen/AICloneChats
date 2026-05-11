/**
 * formatApiError(err, fallback)
 *
 * Safe extractor for `toast.error(...)` and inline error display.
 *
 * Backend auth + credit endpoints return `detail` as the structured
 * `{code, message, request_id}` object. The old pattern
 * `toast.error(err?.response?.data?.detail || "…")` passes the object
 * directly into the toast, which causes React to try to render an object
 * and crash the page blank.
 *
 * Use this everywhere user-facing toasts touch the API.
 */
export function formatApiError(err, fallback = "Something went wrong") {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === "string" ? d : d?.msg || d?.message || "")).filter(Boolean).join("; ") || fallback;
  }
  if (detail && typeof detail === "object") {
    return detail.message || detail.code || fallback;
  }
  if (err?.message === "Network Error") return "Network error — please check your connection.";
  if (err?.response?.status) return `${fallback} (HTTP ${err.response.status})`;
  return fallback;
}
