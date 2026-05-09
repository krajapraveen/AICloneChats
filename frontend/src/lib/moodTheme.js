// Subtle mood theme overlay (NOT a full page background swap).
// Maps mood_ui.theme to a small set of CSS class names + accent tints.
// The premium dark page background remains intact; only chat-bubble accents and
// the optional mood orb tint adapt.

export const MOOD_THEMES = {
  default: {
    pillBg: "rgba(255,255,255,0.05)",
    pillBorder: "rgba(255,255,255,0.14)",
    pillText: "rgba(248,250,252,0.78)",
    accent: "#F59E0B", // amber (default)
    transition: "transition-all duration-300",
  },
  calm: {
    pillBg: "rgba(99,102,241,0.14)",
    pillBorder: "rgba(99,102,241,0.40)",
    pillText: "#C7D2FE", // indigo-200
    accent: "#818CF8",
    transition: "transition-all duration-700",
  },
  soft: {
    pillBg: "rgba(167,139,250,0.14)",
    pillBorder: "rgba(167,139,250,0.40)",
    pillText: "#DDD6FE",
    accent: "#A78BFA",
    transition: "transition-all duration-700",
  },
  bright: {
    pillBg: "rgba(245,158,11,0.16)",
    pillBorder: "rgba(245,158,11,0.45)",
    pillText: "#FCD34D",
    accent: "#F59E0B",
    transition: "transition-all duration-300",
  },
  playful: {
    pillBg: "rgba(236,72,153,0.16)",
    pillBorder: "rgba(236,72,153,0.42)",
    pillText: "#FBCFE8",
    accent: "#EC4899",
    transition: "transition-all duration-300",
  },
  focused: {
    pillBg: "rgba(148,163,184,0.10)",
    pillBorder: "rgba(148,163,184,0.30)",
    pillText: "#CBD5E1",
    accent: "#94A3B8",
    transition: "transition-all duration-500",
  },
};

export function getMoodTheme(name) {
  return MOOD_THEMES[name] || MOOD_THEMES.default;
}
