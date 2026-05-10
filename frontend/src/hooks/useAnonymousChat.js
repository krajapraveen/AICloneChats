/**
 * Anonymous chat hook.
 * - Primary: WebSocket /api/anonymous/ws/:slug?device_id=...
 * - Fallback: long-polling GET /api/anonymous/rooms/:slug/messages?since=<ts>
 * - Auto-reconnect with exponential backoff on WS failure
 * - Falls back to polling after 2 failed WS attempts
 *
 * Returns { messages, status, activeCount, sendMessage, retry, mode }
 *   status: "connecting" | "live" | "polling" | "offline"
 *   mode:   "ws" | "polling"
 */
import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";
import { getDeviceId } from "../lib/deviceId";

const WS_MAX_ATTEMPTS = 2;
const POLL_INTERVAL_MS = 3000;
const RECONNECT_BASE_MS = 1500;

export default function useAnonymousChat(slug) {
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState("connecting"); // connecting | live | polling | offline
  const [mode, setMode] = useState("ws"); // ws | polling
  const [activeCount, setActiveCount] = useState(0);
  const [typingHandles, setTypingHandles] = useState([]);

  const wsRef = useRef(null);
  const wsAttemptsRef = useRef(0);
  const pollTimerRef = useRef(0);
  const lastTsRef = useRef("");
  const stoppedRef = useRef(false);
  const typingTimersRef = useRef({}); // handle -> timeoutId

  const dedupeAndAppend = useCallback((newMsgs) => {
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.message_id));
      const next = [...prev];
      for (const m of newMsgs) {
        if (!m || !m.message_id) continue;
        if (seen.has(m.message_id)) continue;
        seen.add(m.message_id);
        next.push(m);
      }
      next.sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
      const last = next[next.length - 1];
      if (last) lastTsRef.current = last.created_at;
      return next;
    });
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = 0;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollTimerRef.current || stoppedRef.current) return;
    setMode("polling");
    setStatus("polling");
    api.post("/anonymous/track", { event_name: "anonymous_polling_fallback_engaged", metadata: { room_slug: slug } }).catch(() => {});
    const tick = async () => {
      if (stoppedRef.current) return;
      try {
        const since = lastTsRef.current;
        const url = since ? `/anonymous/rooms/${slug}/messages?since=${encodeURIComponent(since)}` : `/anonymous/rooms/${slug}/messages`;
        const { data } = await api.get(url);
        if (data?.messages?.length) dedupeAndAppend(data.messages);
        if (status !== "polling") setStatus("polling");
      } catch (_) {
        setStatus("offline");
      }
    };
    void tick();
    pollTimerRef.current = setInterval(tick, POLL_INTERVAL_MS);
  }, [slug, dedupeAndAppend, status]);

  const connectWs = useCallback(() => {
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
    setStatus("connecting");

    // Hard deadline: if WS hasn't opened in 3s, give up and switch to polling.
    // Polling provides true liveness; once it succeeds the pill flips to "polling".
    const handshakeTimer = setTimeout(() => {
      if (opened || stoppedRef.current) return;
      try { ws.close(); } catch (_) { /* noop */ }
      startPolling();
    }, 3000);

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
          setStatus("live");
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
          setMessages((prev) => prev.filter((m) => m.message_id !== data.message_id));
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
      // If WS never opened, jump straight to polling (handshakeTimer may already have).
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
      setStatus("connecting");
      api.post("/anonymous/track", { event_name: "anonymous_reconnect_attempted", metadata: { room_slug: slug } }).catch(() => {});
      setTimeout(connectWs, delay);
    };
  }, [slug, dedupeAndAppend, startPolling, stopPolling]);

  // Initial: load history, then connect WS
  useEffect(() => {
    stoppedRef.current = false;
    setMessages([]);
    lastTsRef.current = "";
    wsAttemptsRef.current = 0;

    let cancelled = false;
    api.get(`/anonymous/rooms/${slug}/messages`).then(({ data }) => {
      if (cancelled) return;
      if (data?.messages?.length) dedupeAndAppend(data.messages);
      if (data?.room_status === "frozen") setStatus("frozen");
      connectWs();
    }).catch(() => {
      if (!cancelled) startPolling();
    });

    return () => {
      cancelled = true;
      stoppedRef.current = true;
      try { wsRef.current?.close(); } catch (_) { /* noop */ }
      stopPolling();
      Object.values(typingTimersRef.current).forEach((id) => clearTimeout(id));
    };
  }, [slug, connectWs, startPolling, stopPolling, dedupeAndAppend]);

  const sendMessage = useCallback(async (content) => {
    const text = (content || "").trim();
    if (!text) return { status: "skipped" };
    try {
      const { data } = await api.post(`/anonymous/rooms/${slug}/messages`, { content: text });
      // If WS is live, server will broadcast back. If polling, append from response.
      if (data?.status === "allowed" && data.message && mode === "polling") {
        dedupeAndAppend([data.message]);
        if (data.system_message) dedupeAndAppend([data.system_message]);
      }
      return data;
    } catch (err) {
      const detail = err?.response?.data?.detail;
      return { status: "error", error: typeof detail === "string" ? detail : "Could not send message." };
    }
  }, [slug, mode, dedupeAndAppend]);

  const sendTyping = useCallback(() => {
    if (mode !== "ws" || !wsRef.current || wsRef.current.readyState !== 1) return;
    try { wsRef.current.send(JSON.stringify({ type: "typing" })); } catch (_) { /* noop */ }
  }, [mode]);

  const reportMessage = useCallback(async (messageId, reason) => {
    return api.post(`/anonymous/messages/${messageId}/report`, { reason }).then((r) => r.data);
  }, []);

  return { messages, status, mode, activeCount, typingHandles, sendMessage, sendTyping, reportMessage };
}
