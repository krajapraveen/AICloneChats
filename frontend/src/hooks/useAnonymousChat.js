/**
 * Anonymous chat hook.
 * - Primary: WebSocket /api/anonymous/ws/:slug?device_id=...
 * - Fallback: long-polling GET /api/anonymous/rooms/:slug/messages?since=<ts>
 * - Auto-reconnect with exponential backoff on WS failure
 * - Falls back to polling after 2 failed WS attempts (or after a 3s WS handshake deadline)
 *
 * Returns { messages, status, activeCount, sendMessage, retry, mode, typingHandles, sendTyping, reportMessage }
 *   status: "connecting" | "live" | "polling" | "offline" | "frozen"
 *   mode:   "ws" | "polling"
 *
 * BUGFIX 2026-02-12: previous version recreated `connectWs` / `startPolling`
 * on every status change (because `startPolling` depended on `status`).
 * The mounting effect listed those callbacks as deps, so it re-ran on each
 * status transition and called `setMessages([])` → refetch → visible blink
 * of the entire message list during polling/reconnect.
 *
 * Fix: keep all imperative callbacks in refs, depend ONLY on `slug` for the
 * mount effect, and dedupe identical polling payloads so we never replace
 * the messages array unnecessarily.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";
import { getDeviceId } from "../lib/deviceId";
import {
  buildOptimisticMessage,
  markTempFailed,
  mergeIncoming,
  mintTempId,
  reconcileServerEcho,
} from "../lib/chatOptimistic";

const WS_MAX_ATTEMPTS = 2;
const POLL_INTERVAL_MS = 3000;
const RECONNECT_BASE_MS = 1500;
const HANDSHAKE_DEADLINE_MS = 3000;

export default function useAnonymousChat(slug) {
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState("connecting"); // connecting | live | polling | offline | frozen
  const [mode, setMode] = useState("ws"); // ws | polling
  const [activeCount, setActiveCount] = useState(0);
  const [typingHandles, setTypingHandles] = useState([]);

  const wsRef = useRef(null);
  const wsAttemptsRef = useRef(0);
  const pollTimerRef = useRef(0);
  const lastTsRef = useRef("");
  const stoppedRef = useRef(false);
  const typingTimersRef = useRef({}); // handle -> timeoutId
  const modeRef = useRef("ws");
  const statusRef = useRef("connecting");
  const messageIdSetRef = useRef(new Set()); // O(1) dedupe across renders

  // ---- Stable imperative API stored in refs so the mount effect
  // depends ONLY on `slug`. This is what eliminates the blink.
  const connectWsRef = useRef(() => {});
  const startPollingRef = useRef(() => {});
  const stopPollingRef = useRef(() => {});

  /**
   * Append only NEW messages. If nothing actually changes, return the previous
   * array reference — this is critical so React.memo'd bubbles don't repaint.
   */
  const dedupeAndAppend = useCallback((newMsgs) => {
    if (!Array.isArray(newMsgs) || newMsgs.length === 0) return;
    setMessages((prev) => {
      const { next, lastTs } = mergeIncoming(prev, newMsgs, messageIdSetRef.current);
      if (next === prev) return prev; // identity-preserved → memoized bubbles don't repaint
      if (lastTs) lastTsRef.current = lastTs;
      return next;
    });
  }, []);

  // Keep refs of mode/status so callbacks read fresh values without
  // re-binding their identity.
  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => { statusRef.current = status; }, [status]);

  // ---- Build the imperative API once. Stable identity. ----
  useEffect(() => {
    const stopPolling = () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = 0;
      }
    };

    const startPolling = () => {
      if (pollTimerRef.current || stoppedRef.current) return;
      setMode("polling");
      setStatus("polling");
      api
        .post("/anonymous/track", {
          event_name: "anonymous_polling_fallback_engaged",
          metadata: { room_slug: slug },
        })
        .catch(() => {});
      const tick = async () => {
        if (stoppedRef.current) return;
        try {
          const since = lastTsRef.current;
          const url = since
            ? `/anonymous/rooms/${slug}/messages?since=${encodeURIComponent(since)}`
            : `/anonymous/rooms/${slug}/messages`;
          const { data } = await api.get(url);
          // dedupeAndAppend is a no-op if no new messages → prev identity preserved → no repaint
          if (data?.messages?.length) dedupeAndAppend(data.messages);
          // Only flip status if it actually changed.
          if (statusRef.current !== "polling") setStatus("polling");
        } catch (_) {
          if (statusRef.current !== "offline") setStatus("offline");
        }
      };
      void tick();
      pollTimerRef.current = setInterval(tick, POLL_INTERVAL_MS);
    };

    const connectWs = () => {
      if (stoppedRef.current) return;
      const backendUrl = process.env.REACT_APP_BACKEND_URL || "";
      const wsBase = backendUrl.replace(/^http/, "ws");
      const url = `${wsBase}/api/anonymous/ws/${encodeURIComponent(slug)}?device_id=${encodeURIComponent(getDeviceId())}`;
      let ws;
      let opened = false;
      try {
        ws = new WebSocket(url);
      } catch (_) {
        startPolling();
        return;
      }
      wsRef.current = ws;
      setMode("ws");
      // Don't set status to "connecting" if we're already polling successfully.
      // Polling is the authoritative liveness signal once it begins.
      if (statusRef.current !== "polling") setStatus("connecting");

      // Hard deadline: if WS hasn't opened in 3s, give up and switch to polling.
      const handshakeTimer = setTimeout(() => {
        if (opened || stoppedRef.current) return;
        try { ws.close(); } catch (_) { /* noop */ }
        startPolling();
      }, HANDSHAKE_DEADLINE_MS);

      ws.onopen = () => {
        opened = true;
        clearTimeout(handshakeTimer);
        wsAttemptsRef.current = 0;
        stopPolling(); // WS is authoritative if it opens
        setMode("ws");
        setStatus("live");
      };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "hello") {
            if (Array.isArray(data.messages)) dedupeAndAppend(data.messages);
            if (typeof data.active_count === "number") setActiveCount(data.active_count);
            if (statusRef.current !== "live") setStatus("live");
          } else if (data.type === "new_message") {
            if (data.message) dedupeAndAppend([data.message]);
          } else if (data.type === "active_count") {
            setActiveCount(data.count || 0);
          } else if (data.type === "typing") {
            const h = data.handle;
            if (!h) return;
            setTypingHandles((prev) => (prev.includes(h) ? prev : [...prev, h]));
            if (typingTimersRef.current[h]) clearTimeout(typingTimersRef.current[h]);
            typingTimersRef.current[h] = setTimeout(() => {
              setTypingHandles((prev) => prev.filter((x) => x !== h));
            }, 2500);
          } else if (data.type === "message_removed") {
            setMessages((prev) => {
              const next = prev.filter((m) => m.message_id !== data.message_id);
              if (next.length === prev.length) return prev;
              messageIdSetRef.current.delete(data.message_id);
              return next;
            });
          } else if (data.type === "room_frozen") {
            setStatus("frozen");
          }
        } catch (_) { /* ignore */ }
      };
      ws.onerror = () => {
        // onclose will handle the fallback
      };
      ws.onclose = () => {
        clearTimeout(handshakeTimer);
        if (stoppedRef.current) return;
        // If WS never opened, jump straight to polling.
        if (!opened) {
          startPolling();
          return;
        }
        // WS was live and dropped — try one quick reconnect, else polling.
        wsAttemptsRef.current += 1;
        if (wsAttemptsRef.current >= WS_MAX_ATTEMPTS) {
          startPolling();
          return;
        }
        const delay = RECONNECT_BASE_MS * wsAttemptsRef.current;
        // Don't downgrade UI to "connecting" if we already started polling — keeps the pill calm.
        if (statusRef.current === "live") setStatus("connecting");
        api
          .post("/anonymous/track", {
            event_name: "anonymous_reconnect_attempted",
            metadata: { room_slug: slug },
          })
          .catch(() => {});
        setTimeout(connectWs, delay);
      };
    };

    connectWsRef.current = connectWs;
    startPollingRef.current = startPolling;
    stopPollingRef.current = stopPolling;
  }, [slug, dedupeAndAppend]);

  // ---- Single mount/teardown per slug ----
  // Critical: deps are ONLY [slug]. Status changes do NOT retrigger this.
  useEffect(() => {
    stoppedRef.current = false;
    setMessages([]);
    messageIdSetRef.current = new Set();
    lastTsRef.current = "";
    wsAttemptsRef.current = 0;

    let cancelled = false;
    api
      .get(`/anonymous/rooms/${slug}/messages`)
      .then(({ data }) => {
        if (cancelled) return;
        if (data?.messages?.length) dedupeAndAppend(data.messages);
        if (data?.room_status === "frozen") setStatus("frozen");
        connectWsRef.current();
      })
      .catch(() => {
        if (!cancelled) startPollingRef.current();
      });

    return () => {
      cancelled = true;
      stoppedRef.current = true;
      try { wsRef.current?.close(); } catch (_) { /* noop */ }
      stopPollingRef.current();
      Object.values(typingTimersRef.current).forEach((id) => clearTimeout(id));
      typingTimersRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const sendMessage = useCallback(async (content, sessionInfo) => {
    const text = (content || "").trim();
    if (!text) return { status: "skipped" };

    // ---- 1. Optimistic insert ----
    // The user MUST see their message in the chat the instant they hit Send.
    // We mint a tempId, append a pending bubble, then reconcile when the POST
    // returns (or fail it visibly on error).
    const tempId = mintTempId();
    const tempMessage = buildOptimisticMessage({
      tempId,
      content: text,
      sessionId: sessionInfo?.session_id,
      handle: sessionInfo?.anonymous_handle,
    });
    setMessages((prev) => prev.concat(tempMessage));

    try {
      const { data } = await api.post(`/anonymous/rooms/${slug}/messages`, { content: text });
      if (data?.status === "allowed" && data.message) {
        setMessages((prev) => reconcileServerEcho(prev, tempId, data.message));
        messageIdSetRef.current.add(data.message.message_id);
        if (data.system_message) dedupeAndAppend([data.system_message]);
      } else if (data?.status === "blocked" || data?.status === "error") {
        setMessages((prev) => markTempFailed(prev, tempId, data.human_reason || data.reason || "Blocked"));
      }
      return data;
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const errMsg = typeof detail === "string" ? detail : "Message could not be sent. Try again.";
      setMessages((prev) => markTempFailed(prev, tempId, errMsg));
      return { status: "error", error: errMsg };
    }
  }, [slug, dedupeAndAppend]);

  const sendTyping = useCallback(() => {
    if (modeRef.current !== "ws" || !wsRef.current || wsRef.current.readyState !== 1) return;
    try { wsRef.current.send(JSON.stringify({ type: "typing" })); } catch (_) { /* noop */ }
  }, []);

  const reportMessage = useCallback(async (messageId, reason) => {
    return api.post(`/anonymous/messages/${messageId}/report`, { reason }).then((r) => r.data);
  }, []);

  return { messages, status, mode, activeCount, typingHandles, sendMessage, sendTyping, reportMessage };
}
