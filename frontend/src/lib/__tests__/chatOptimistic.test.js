/**
 * Regression: anonymous chat optimistic-send reconciliation.
 *
 * Bug history (2026-05-11):
 *   Anonymous Chat send was not optimistic — the user's message only
 *   appeared after the server round-trip + WS broadcast landed. Felt broken.
 *
 *   These tests lock the helpers in `lib/chatOptimistic.js` that the hook
 *   `useAnonymousChat` relies on for: optimistic insert, server-echo
 *   reconciliation, race-resilient dedupe (POST-first AND WS-first), and
 *   failure handling. Polling/WS arrival of the canonical message must NOT
 *   leave a duplicate alongside the temp message.
 */
import {
  buildOptimisticMessage,
  markTempFailed,
  mergeIncoming,
  mintTempId,
  reconcileServerEcho,
} from "../chatOptimistic";

describe("mintTempId", () => {
  test("returns a string prefixed with temp_", () => {
    const id = mintTempId();
    expect(typeof id).toBe("string");
    expect(id.startsWith("temp_")).toBe(true);
  });

  test("two calls return distinct ids", () => {
    const a = mintTempId();
    const b = mintTempId();
    expect(a).not.toBe(b);
  });
});

describe("buildOptimisticMessage", () => {
  test("shape mimics server message with pending=true and temp_id=message_id", () => {
    const msg = buildOptimisticMessage({
      tempId: "temp_x",
      content: "hello",
      sessionId: "s1",
      handle: "WILDGROVE49",
    });
    expect(msg.message_id).toBe("temp_x");
    expect(msg.temp_id).toBe("temp_x");
    expect(msg.content).toBe("hello");
    expect(msg.session_id).toBe("s1");
    expect(msg.anonymous_handle).toBe("WILDGROVE49");
    expect(msg.pending).toBe(true);
    expect(msg.message_type).toBe("user");
    expect(typeof msg.created_at).toBe("string");
  });

  test("handle defaults to 'YOU' if missing", () => {
    const msg = buildOptimisticMessage({ tempId: "temp_x", content: "hi" });
    expect(msg.anonymous_handle).toBe("YOU");
  });
});

describe("reconcileServerEcho — POST-first path", () => {
  test("replaces temp with server-echoed message", () => {
    const temp = buildOptimisticMessage({ tempId: "temp_a", content: "hi", sessionId: "s1" });
    const prev = [temp];
    const server = {
      message_id: "srv_1",
      content: "hi",
      session_id: "s1",
      anonymous_handle: "WILDGROVE49",
      created_at: new Date().toISOString(),
    };
    const next = reconcileServerEcho(prev, "temp_a", server);
    expect(next).toHaveLength(1);
    expect(next[0].message_id).toBe("srv_1");
    expect(next[0].temp_id).toBeUndefined();
    expect(next[0].pending).toBeUndefined();
  });

  test("no temp present → no-op (returns prev identity)", () => {
    const prev = [{ message_id: "m1", content: "hi" }];
    const server = { message_id: "srv_1", content: "hi" };
    const next = reconcileServerEcho(prev, "temp_a", server);
    expect(next).toBe(prev);
  });

  test("server message already present (WS won race) → drops temp without duplicating", () => {
    const temp = buildOptimisticMessage({ tempId: "temp_a", content: "hi", sessionId: "s1" });
    const wsCanonical = {
      message_id: "srv_1",
      content: "hi",
      session_id: "s1",
      anonymous_handle: "WILDGROVE49",
      created_at: new Date().toISOString(),
    };
    const prev = [wsCanonical, temp];
    const next = reconcileServerEcho(prev, "temp_a", wsCanonical);
    expect(next).toHaveLength(1);
    expect(next[0].message_id).toBe("srv_1");
    expect(next.find((m) => m.temp_id === "temp_a")).toBeUndefined();
  });
});

describe("mergeIncoming — WS/polling arrival reconciliation", () => {
  function freshSeen(...ids) {
    return new Set(ids);
  }

  test("simple append when no temp is pending", () => {
    const prev = [];
    const seen = freshSeen();
    const incoming = [
      { message_id: "m1", content: "hello", session_id: "other", created_at: "2026-01-01T00:00:01Z" },
    ];
    const { next, lastTs } = mergeIncoming(prev, incoming, seen);
    expect(next).toHaveLength(1);
    expect(next[0].message_id).toBe("m1");
    expect(lastTs).toBe("2026-01-01T00:00:01Z");
    expect(seen.has("m1")).toBe(true);
  });

  test("WS arrives BEFORE POST response → drops pending temp, no duplicate", () => {
    const temp = buildOptimisticMessage({ tempId: "temp_a", content: "hi", sessionId: "s1" });
    const prev = [temp];
    const seen = freshSeen();
    const incoming = [
      { message_id: "srv_1", content: "hi", session_id: "s1", anonymous_handle: "WILDGROVE49", created_at: "2026-01-01T00:00:02Z" },
    ];
    const { next } = mergeIncoming(prev, incoming, seen);
    expect(next).toHaveLength(1);
    expect(next[0].message_id).toBe("srv_1");
    // Crucial: the message must NOT appear twice
    const ids = next.map((m) => m.message_id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  test("already-seen id is filtered out (no duplicate ever)", () => {
    const prev = [{ message_id: "srv_1", content: "hi" }];
    const seen = freshSeen("srv_1");
    const incoming = [{ message_id: "srv_1", content: "hi" }];
    const { next } = mergeIncoming(prev, incoming, seen);
    expect(next).toBe(prev); // identity preserved → no repaint
  });

  test("sorts merged messages by created_at", () => {
    const prev = [
      { message_id: "a", content: "first", created_at: "2026-01-01T00:00:00Z" },
    ];
    const seen = freshSeen("a");
    const incoming = [
      { message_id: "c", content: "third", created_at: "2026-01-01T00:00:02Z" },
      { message_id: "b", content: "second", created_at: "2026-01-01T00:00:01Z" },
    ];
    const { next } = mergeIncoming(prev, incoming, seen);
    expect(next.map((m) => m.message_id)).toEqual(["a", "b", "c"]);
  });

  test("does not drop temp if session_id differs (different user)", () => {
    const temp = buildOptimisticMessage({ tempId: "temp_a", content: "hi", sessionId: "s1" });
    const prev = [temp];
    const seen = freshSeen();
    // Same content but from a different session — must NOT collapse
    const incoming = [
      { message_id: "srv_X", content: "hi", session_id: "s2", created_at: "2026-01-01T00:00:01Z" },
    ];
    const { next } = mergeIncoming(prev, incoming, seen);
    expect(next.find((m) => m.temp_id === "temp_a")).toBeDefined();
    expect(next.find((m) => m.message_id === "srv_X")).toBeDefined();
    expect(next).toHaveLength(2);
  });
});

describe("markTempFailed — backend failure UX", () => {
  test("marks the matching temp as failed without losing content", () => {
    const temp = buildOptimisticMessage({ tempId: "temp_a", content: "hi", sessionId: "s1" });
    const prev = [temp];
    const next = markTempFailed(prev, "temp_a", "rate_limited");
    expect(next).toHaveLength(1);
    expect(next[0].pending).toBe(false);
    expect(next[0].failed).toBe(true);
    expect(next[0].error).toBe("rate_limited");
    expect(next[0].content).toBe("hi"); // user text preserved
  });

  test("idempotent when temp is missing", () => {
    const prev = [{ message_id: "x", content: "x" }];
    const next = markTempFailed(prev, "temp_a", "err");
    expect(next).toEqual(prev);
  });
});
