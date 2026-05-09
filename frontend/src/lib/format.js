export function formatCount(n) {
  const num = Number(n) || 0;
  if (num < 1000) return String(num);
  if (num < 10000) return (num / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  if (num < 1000000) return Math.floor(num / 1000) + "k";
  return (num / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
}

export const MOOD_META = {
  funny: { emoji: "😂", label: "Funny", color: "tag-amber" },
  deep: { emoji: "🧠", label: "Deep", color: "tag-violet" },
  savage: { emoji: "🔥", label: "Savage", color: "tag-rose" },
  quote: { emoji: "✨", label: "Quotable", color: "tag-emerald" },
};
