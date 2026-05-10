/**
 * Debate room polling hook.
 *
 * Polling-only (no WS) — we already learned from Anonymous Reality that
 * preview-environment WS handshakes are unreliable and the polling path is
 * what users actually experience. Skipping WS keeps the implementation
 * predictable and avoids the flicker class of bugs entirely.
 *
 * Identity-preserving updates:
 * - argumentsList state is replaced ONLY when payload actually changed
 * - leaderboard state replaced ONLY when generated_at differs
 * - Components that consume these can safely use React.memo
 *
 * Returns: { debate, args, leaderboard, status, refresh, error }
 *   status: "loading" | "live" | "offline"
 */
import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";

const POLL_INTERVAL_MS = 5000;

function shallowEqualArgs(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i];
    if (
      x.argument_id !== y.argument_id ||
      x.upvotes !== y.upvotes ||
      x.downvotes !== y.downvotes ||
      x.rank_score !== y.rank_score ||
      x.moderation_status !== y.moderation_status ||
      x.my_vote !== y.my_vote
    ) return false;
  }
  return true;
}

export default function useDebateRoom(slug) {
  const [debate, setDebate] = useState(null);
  const [args, setArgs] = useState([]);
  const [leaderboard, setLeaderboard] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | live | offline
  const [error, setError] = useState("");

  const stoppedRef = useRef(false);
  const timerRef = useRef(0);
  const lastLbAtRef = useRef("");

  const fetchAll = useCallback(async () => {
    try {
      const [d, a, lb] = await Promise.all([
        api.get(`/debates/${slug}`),
        api.get(`/debates/${slug}/arguments`),
        api.get(`/debates/${slug}/leaderboard`),
      ]);
      if (stoppedRef.current) return;
      setDebate(d.data);
      setArgs((prev) => (shallowEqualArgs(prev, a.data?.arguments || []) ? prev : (a.data?.arguments || [])));
      const newLb = lb.data;
      if (newLb?.generated_at && newLb.generated_at !== lastLbAtRef.current) {
        lastLbAtRef.current = newLb.generated_at;
        setLeaderboard(newLb);
      } else if (!leaderboard && newLb) {
        setLeaderboard(newLb);
      }
      setStatus("live");
      setError("");
    } catch (e) {
      if (stoppedRef.current) return;
      const detail = e?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "Could not load debate.");
      setStatus("offline");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  useEffect(() => {
    stoppedRef.current = false;
    setStatus("loading");
    setArgs([]);
    setLeaderboard(null);
    lastLbAtRef.current = "";

    fetchAll();
    timerRef.current = setInterval(fetchAll, POLL_INTERVAL_MS);
    return () => {
      stoppedRef.current = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [slug, fetchAll]);

  const refresh = useCallback(() => fetchAll(), [fetchAll]);

  return { debate, args, leaderboard, status, error, refresh };
}
