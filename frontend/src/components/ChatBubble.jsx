export default function ChatBubble({ sender, text, name, onShare, shareWorthy }) {
  const isClone = sender === "clone";
  return (
    <div className={`flex ${isClone ? "justify-start" : "justify-end"} animate-fade-up group`} data-testid={`chat-bubble-${sender}`}>
      <div className="flex flex-col gap-1 max-w-[88%]">
        {name && (
          <span className={`text-[10px] font-display font-bold uppercase tracking-wider text-muted ${isClone ? "" : "text-right"}`}>
            {name}
          </span>
        )}
        <div className="flex items-end gap-2">
          {isClone && shareWorthy && (
            <span className="share-worthy-badge animate-sparkle" data-testid="share-worthy-badge" title="Share-worthy reply">
              ✨ share
            </span>
          )}
          <div className={isClone ? "chat-bubble-clone" : "chat-bubble-visitor"}>
            {text}
          </div>
          {isClone && onShare && (
            <button
              onClick={onShare}
              className="opacity-0 group-hover:opacity-100 transition opacity-100 sm:opacity-0 sm:group-hover:opacity-100 flex-shrink-0 w-8 h-8 rounded-full bg-white/5 border border-white/10 hover:bg-amber/20 hover:border-amber/40 hover:text-amber-soft text-ink/60 flex items-center justify-center"
              data-testid="share-message-btn"
              aria-label="Share this reply"
              title="Share this reply as an image"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                <polyline points="16 6 12 2 8 6" />
                <line x1="12" y1="2" x2="12" y2="15" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
