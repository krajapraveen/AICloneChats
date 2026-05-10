/**
 * Translation Chat — polling-based realtime hook.
 * Mirrors useDebateRoom: identity-preserving updates, no flicker.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";

const POLL_MS = 4000;

function getOrCreateDeviceId() {
  if (typeof window === "undefined") return "";
  const KEY = "tx_device_id";
  let id = window.localStorage.getItem(KEY);
  if (!id) {
    const rand = (typeof crypto !== "undefined" && crypto.randomUUID) ? crypto.randomUUID() : `dev-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    id = `tx-${rand.replace(/-/g, "").slice(0, 24)}`;
    window.localStorage.setItem(KEY, id);
  }
  return id;
}

function withDevice(headers = {}) {
  return { ...headers, "X-Tx-Device-Id": getOrCreateDeviceId() };
}

function shallowEqualMsgs(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].message_id !== b[i].message_id || a[i].display_text !== b[i].display_text || a[i].moderation_status !== b[i].moderation_status) return false;
  }
  return true;
}

export default function useTranslationChat(roomId) {
  const [room, setRoom] = useState(null);
  const [messages, setMessages] = useState([]);
  const [members, setMembers] = useState([]);
  const [me, setMe] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | live | offline | not-joined
  const [error, setError] = useState("");

  const stoppedRef = useRef(false);
  const lastTsRef = useRef("");
  const seenIdsRef = useRef(new Set());
  const pollRef = useRef(0);

  const refreshRoom = useCallback(async () => {
    try {
      const r = await api.get(`/translation-chat/rooms/${roomId}`, { headers: withDevice() });
      if (stoppedRef.current) return;
      setRoom(r.data?.room || null);
      setMembers(r.data?.members || []);
      setMe(r.data?.me || null);
      setStatus(r.data?.me ? "live" : "not-joined");
    } catch (e) {
      if (!stoppedRef.current) {
        setStatus("offline");
        setError(e?.response?.data?.detail || "Could not load room.");
      }
    }
  }, [roomId]);

  const fetchMessages = useCallback(async () => {
    if (!roomId) return;
    try {
      const sinceParam = lastTsRef.current ? `?since=${encodeURIComponent(lastTsRef.current)}` : "";
      const r = await api.get(`/translation-chat/rooms/${roomId}/messages${sinceParam}`, { headers: withDevice() });
      if (stoppedRef.current) return;
      const incoming = r.data?.messages || [];
      if (!incoming.length) return;
      setMessages((prev) => {
        const seen = seenIdsRef.current;
        const fresh = [];
        for (const m of incoming) {
          if (!m.message_id || seen.has(m.message_id)) continue;
          seen.add(m.message_id);
          fresh.push(m);
        }
        if (fresh.length === 0) return prev;
        const next = [...prev, ...fresh];
        const last = next[next.length - 1];
        if (last) lastTsRef.current = last.created_at;
        return next;
      });
    } catch (e) {
      if (e?.response?.status === 403) setStatus("not-joined");
    }
  }, [roomId]);

  useEffect(() => {
    if (!roomId) return;
    stoppedRef.current = false;
    setMessages([]);
    seenIdsRef.current = new Set();
    lastTsRef.current = "";
    refreshRoom().then(() => fetchMessages());
    pollRef.current = setInterval(() => {
      refreshRoom();
      fetchMessages();
    }, POLL_MS);
    return () => {
      stoppedRef.current = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [roomId, refreshRoom, fetchMessages]);

  const join = useCallback(async (display_name, preferred_language) => {
    await api.post(`/translation-chat/rooms/${roomId}/join`, { display_name, preferred_language }, { headers: withDevice() });
    api.post(`/translation-chat/rooms/${roomId}/track`, { event_name: "translation_room_joined", metadata: { preferred_language } }, { headers: withDevice() }).catch(() => {});
    await refreshRoom();
    // Reload messages with new target language
    seenIdsRef.current = new Set();
    lastTsRef.current = "";
    setMessages([]);
    await fetchMessages();
  }, [roomId, refreshRoom, fetchMessages]);

  const switchLanguage = useCallback(async (lang) => {
    await api.patch(`/translation-chat/rooms/${roomId}/language`, { preferred_language: lang }, { headers: withDevice() });
    seenIdsRef.current = new Set();
    lastTsRef.current = "";
    setMessages([]);
    await refreshRoom();
    await fetchMessages();
  }, [roomId, refreshRoom, fetchMessages]);

  const send = useCallback(async (content) => {
    const r = await api.post(`/translation-chat/rooms/${roomId}/messages`, { content }, { headers: withDevice() });
    const msg = r.data?.message;
    if (msg && !seenIdsRef.current.has(msg.message_id)) {
      seenIdsRef.current.add(msg.message_id);
      setMessages((prev) => {
        const next = [...prev, msg];
        lastTsRef.current = msg.created_at;
        return shallowEqualMsgs(prev, next) ? prev : next;
      });
    }
    return msg;
  }, [roomId]);

  return { room, me, members, messages, status, error, join, switchLanguage, send, refresh: refreshRoom };
}
