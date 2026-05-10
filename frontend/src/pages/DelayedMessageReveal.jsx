/**
 * Reveal page for an emailed delayed-delivery message.
 *
 * Recipients without an account land here via the link in their email.
 * The page is intentionally noindex/nocache: this is private correspondence,
 * not crawler bait. There is no follow-up CTA, no signup wall, no "subscribe
 * for more". The system delivered; the user reads. That is the entire loop.
 */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const CATEGORY_COPY = {
  future_self: "for your future self",
  apology: "an apology",
  memory: "a memory",
  motivation: "motivation",
  love: "love",
  grief: "grief",
  custom: "a message",
};

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function DelayedMessageReveal() {
  const { token } = useParams();
  const [state, setState] = useState({ loading: true, error: null, message: null });

  // Inject noindex + nocache directives at runtime. SPA routing means the
  // index.html meta is shared across routes, so we mutate the document head
  // on mount and clean up on unmount.
  useEffect(() => {
    const tags = [];
    const robots = document.createElement("meta");
    robots.setAttribute("name", "robots");
    robots.setAttribute("content", "noindex, nofollow, noarchive, nosnippet");
    document.head.appendChild(robots);
    tags.push(robots);

    const cache = document.createElement("meta");
    cache.setAttribute("http-equiv", "Cache-Control");
    cache.setAttribute("content", "no-store, no-cache, must-revalidate, private");
    document.head.appendChild(cache);
    tags.push(cache);

    const referrer = document.createElement("meta");
    referrer.setAttribute("name", "referrer");
    referrer.setAttribute("content", "no-referrer");
    document.head.appendChild(referrer);
    tags.push(referrer);

    const prevTitle = document.title;
    document.title = "A message for you · aiclonechats.com";

    return () => {
      tags.forEach((t) => t.parentNode && t.parentNode.removeChild(t));
      document.title = prevTitle;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await axios.get(`${BACKEND_URL}/api/delayed-messages/open/${encodeURIComponent(token)}`);
        if (!cancelled) setState({ loading: false, error: null, message: res.data?.delayed_message || null });
      } catch (err) {
        if (cancelled) return;
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail || "This link is not valid.";
        setState({ loading: false, error: { status, detail }, message: null });
      }
    }
    load();
    return () => { cancelled = true; };
  }, [token]);

  return (
    <div className="min-h-screen page-bg flex items-start justify-center py-10 px-4" data-testid="delayed-reveal-page">
      <div className="w-full max-w-2xl">
        <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-muted mb-3" data-testid="delayed-reveal-eyebrow">
          aiclonechats.com · delayed emotional chat
        </div>

        {state.loading && (
          <div className="brutal-card p-6" data-testid="delayed-reveal-loading">
            <div className="text-sm font-mono text-muted">Opening the message…</div>
          </div>
        )}

        {!state.loading && state.error && (
          <div className="brutal-card p-6 border-amber/40 bg-amber-500/10" data-testid="delayed-reveal-error">
            <div className="text-amber font-mono text-xs uppercase tracking-widest mb-2">
              {state.error.status === 403 ? "Sealed" : "Not available"}
            </div>
            <div className="text-sm whitespace-pre-wrap">
              {state.error.status === 403
                ? "This message is sealed until its delivery time. Try the link again after the time it was scheduled for."
                : "This link is not valid, has been removed, or never existed. There is nothing to retry — the link is the only key."}
            </div>
          </div>
        )}

        {!state.loading && !state.error && state.message && (
          <article className="brutal-card p-6 sm:p-8 space-y-6" data-testid="delayed-reveal-card">
            <header className="space-y-2">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-muted" data-testid="delayed-reveal-category">
                {CATEGORY_COPY[state.message.emotional_category] || "a message"}
              </div>
              <h1 className="text-2xl sm:text-3xl font-semibold leading-tight" data-testid="delayed-reveal-title">
                {state.message.title}
              </h1>
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted" data-testid="delayed-reveal-delivered-at">
                Delivered · {formatDate(state.message.delivered_at)}
              </div>
            </header>

            <div className="text-base leading-relaxed whitespace-pre-wrap" data-testid="delayed-reveal-body">
              {state.message.message_body}
            </div>

            <footer className="pt-4 border-t border-ink/10 text-[11px] font-mono uppercase tracking-widest text-muted" data-testid="delayed-reveal-footer">
              The system delivers; it does not chase.
            </footer>
          </article>
        )}
      </div>
    </div>
  );
}
