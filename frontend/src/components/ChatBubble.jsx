export default function ChatBubble({ sender, text, name }) {
  const isClone = sender === "clone";
  return (
    <div className={`flex ${isClone ? "justify-start" : "justify-end"} animate-fade-up`} data-testid={`chat-bubble-${sender}`}>
      <div className="flex flex-col gap-1 max-w-[85%]">
        {name && (
          <span className={`text-[10px] font-display font-bold uppercase tracking-wider text-muted-foreground ${isClone ? "" : "text-right"}`}>
            {name}
          </span>
        )}
        <div className={isClone ? "chat-bubble-clone" : "chat-bubble-visitor"}>
          {text}
        </div>
      </div>
    </div>
  );
}
