import { useMemo, useState, useCallback } from "react";
import { getMoodTheme } from "../lib/moodTheme";

const MOOD_ENABLED = (process.env.REACT_APP_MOOD_CHAT_ENABLED || "true").toLowerCase() !== "false";

const DEFAULT_MOOD = {
  enabled: MOOD_ENABLED,
  dominant_state: "neutral",
  theme: "default",
  confidence: 0,
  animation_level: "normal",
  show_mood_pill: false,
  microcopy: null,
};

export function useMoodTheme() {
  const [moodUI, setMoodUI] = useState(DEFAULT_MOOD);

  const theme = useMemo(() => {
    if (!moodUI?.enabled || !MOOD_ENABLED) return getMoodTheme("default");
    return getMoodTheme(moodUI.theme);
  }, [moodUI]);

  const updateMoodUI = useCallback((next) => {
    if (!next || !MOOD_ENABLED) return;
    setMoodUI((prev) => {
      if (next.enabled === false) return { ...DEFAULT_MOOD, enabled: false };
      // Flicker guard: if next is below threshold, keep prev theme but lower the pill
      if ((next.confidence || 0) < 0.65) {
        return {
          ...prev,
          confidence: next.confidence || 0,
          show_mood_pill: false,
          microcopy: null,
        };
      }
      return next;
    });
  }, []);

  return { moodUI, theme, updateMoodUI, isEnabled: MOOD_ENABLED };
}
