/**
 * Pure helpers for Anonymous chat optimistic-send reconciliation.
 *
 * Extracted from useAnonymousChat.js so the reconciliation rules can be
 * unit-tested without React/router/network plumbing.
 *
 * Bug history (2026-05-11):
 *   Anonymous Chat send was not optimistic — user message only appeared
 *   after the server round-trip + WS broadcast settled. Felt broken.
 *   Fix: optimistic insert with `temp_id`, plus reconciliation logic that
 *   collapses the temp when the server-echoed message arrives.
 */

/**
 * Build a stable temp-message id from a clock + random nibble.
 * Exported for callers that need to mint ids outside the helpers below.
 */
export function mintTempId() {
  const rnd =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  return `temp_${rnd}_${Date.now().toString(36)}`;
}

/**
 * Build an optimistic message object that mimics the server shape so the
 * existing MessageBubble renders it without special-casing.
 */
export function buildOptimisticMessage({ tempId, content, sessionId, handle }) {
  return {
    message_id: tempId,
    temp_id: tempId,
    content,
    created_at: new Date().toISOString(),
    message_type: "user",
    anonymous_handle: handle || "YOU",
    session_id: sessionId || "",
    pending: true,
  };
}

/**
 * Reconcile a list of incoming server-broadcast messages with the current
 * messages array, dropping any pending optimistic temp whose content +
 * session_id matches a fresh server message.
 *
 * Inputs:
 *   prev        - current messages array
 *   newMsgs     - server-broadcast messages
 *   seenIdsSet  - mutable Set of message_ids already rendered (dedupe scratch)
 *
 * Output: { next, lastTs }
 *   next   - reconciled, sorted-by-created_at array (or prev if no change)
 *   lastTs - the most recent created_at after merge (or null)
 */
export function mergeIncoming(prev, newMsgs, seenIdsSet) {
  if (!Array.isArray(newMsgs) || newMsgs.length === 0) return { next: prev, lastTs: null };
  const pendingByContent = new Map();
  for (let i = 0; i < prev.length; i++) {
    const p = prev[i];
    if (p?.temp_id && p?.pending) {
      pendingByContent.set(`${p.session_id || ""}::${p.content}`, i);
    }
  }
  const incoming = [];
  const tempIndicesToDrop = new Set();
  for (const m of newMsgs) {
    if (!m || !m.message_id) continue;
    if (seenIdsSet.has(m.message_id)) continue;
    const k = `${m.session_id || ""}::${m.content}`;
    if (m.session_id && pendingByContent.has(k)) {
      tempIndicesToDrop.add(pendingByContent.get(k));
    }
    seenIdsSet.add(m.message_id);
    incoming.push(m);
  }
  if (incoming.length === 0 && tempIndicesToDrop.size === 0) {
    return { next: prev, lastTs: null };
  }
  let base = prev;
  if (tempIndicesToDrop.size > 0) {
    base = prev.filter((_, i) => !tempIndicesToDrop.has(i));
  }
  const next = base.concat(incoming);
  next.sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
  const last = next[next.length - 1];
  return { next, lastTs: last?.created_at || null };
}

/**
 * Resolve the temp-message replacement after a successful POST. Returns the
 * new messages array. Idempotent if temp is already gone (e.g., the WS
 * broadcast won the race and `mergeIncoming` already dropped it).
 *
 *   prev          - current messages array
 *   tempId        - the optimistic temp_id
 *   serverMessage - { message_id, content, anonymous_handle, ... }
 */
export function reconcileServerEcho(prev, tempId, serverMessage) {
  const serverId = serverMessage?.message_id;
  if (!serverId) return prev;
  const serverAlreadyPresent = prev.some((m) => m.message_id === serverId);
  const idx = prev.findIndex((m) => m.temp_id === tempId);
  if (serverAlreadyPresent) {
    if (idx === -1) return prev;
    const next = prev.slice();
    next.splice(idx, 1);
    return next;
  }
  if (idx === -1) return prev;
  const next = prev.slice();
  next[idx] = { ...serverMessage };
  return next;
}

/**
 * Apply a failed state to the matching temp message so the UI can show
 * "failed" + error copy without losing the user's text.
 */
export function markTempFailed(prev, tempId, error) {
  return prev.map((m) =>
    m.temp_id === tempId ? { ...m, pending: false, failed: true, error } : m,
  );
}
