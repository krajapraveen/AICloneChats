import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import InfoIcon from "./InfoIcon";
import ChatInfoModal from "./ChatInfoModal";

const CHAT_INFO = {
  clone: {
    id: "clone",
    kicker: "OPTION 01",
    title: "What is AI Clone Chat?",
    body:
      "AI Clone Chat lets you talk with a created AI personality. The clone replies using its configured bio, tone, catchphrases, allowed topics, and blocked topics — so it sounds like that specific person, not a generic assistant.",
    how_to: [
      "Choose an existing clone from Explore — or create your own.",
      "Configure bio, personality sliders, allowed/blocked topics, and memories.",
      "Open the public clone link.",
      "Type a message or question.",
      "The clone replies in its selected personality style.",
      "Edit clone settings anytime to improve replies.",
    ],
    example: {
      input: "Give me startup advice.",
      output: "Here's the blunt version: stop building extra features and test demand first.",
    },
    safety:
      "The clone is for entertainment, coaching, education, and productivity. It should not impersonate real people without permission, and it does not give safety-critical medical, legal, or financial advice.",
  },
  mood: {
    id: "mood",
    kicker: "OPTION 02",
    title: "What is Mood-Based Chat?",
    body:
      "Mood-Based Chat adapts the conversation style based on your emotional tone. If you sound stressed, sad, frustrated, excited, playful, or calm, the chat softens, energizes, or grounds itself to match — without impersonating any specific person.",
    how_to: [
      "Open Mood-Based Chat.",
      "Type naturally — no setup required.",
      "The system detects the emotional tone of your message.",
      "Replies become softer, calmer, more supportive, playful, or energetic depending on the mood.",
      "A subtle mood signal appears when confidence is high.",
    ],
    example: {
      input: "I'm feeling overwhelmed and nothing is working.",
      output:
        "Let's slow this down. You don't need to solve everything at once. Tell me the one thing causing the most pressure right now.",
    },
    safety:
      "Mood-Based Chat is not therapy or emergency support. If you mention self-harm or danger, the chat will respond supportively and encourage you to reach a trusted person or local emergency services immediately.",
  },
};

export default function ChatTypeCards() {
  const navigate = useNavigate();
  const [openInfo, setOpenInfo] = useState(null); // 'clone' | 'mood' | null

  useEffect(() => {
    api.post("/analytics/event", { event_name: "chat_type_card_viewed" }).catch(() => {});
  }, []);

  const select = (chat_type) => {
    api.post("/analytics/event", { event_name: "chat_type_selected", metadata: { chat_type } }).catch(() => {});
    if (chat_type === "clone") {
      api.post("/analytics/event", { event_name: "clone_chat_started", metadata: { source: "chat_type_cards" } }).catch(() => {});
      navigate("/explore");
    } else {
      api.post("/analytics/event", { event_name: "mood_chat_started", metadata: { source: "chat_type_cards" } }).catch(() => {});
      navigate("/mood-chat");
    }
  };

  return (
    <section className="border-t border-white/5" data-testid="chat-type-cards-section">
      <div className="max-w-6xl mx-auto px-5 md:px-8 py-16 md:py-24">
        <div className="max-w-2xl mb-10">
          <span className="tag mb-4 inline-block">Pick your chat</span>
          <h2 className="heading-display text-3xl md:text-5xl mb-3">Two ways to talk.</h2>
          <p className="text-muted font-medium leading-relaxed">
            Personality-first, or emotion-first. Choose the one that fits the moment — they work in completely different ways.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* AI Clone Chat */}
          <div className="brutal-card p-7 flex flex-col" data-testid="card-clone-chat">
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="tag tag-amber">PERSONALITY-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("clone")} label="What is AI Clone Chat?" testId="info-icon-clone" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">AI Clone Chat</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              Chat with a real person's AI clone — their tone, humor, opinions, and weird takes. Browse existing clones or build your own.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Each clone has a distinct personality</li>
              <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Configurable bio, tone, memories</li>
              <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Public share link per clone</li>
            </ul>
            <button onClick={() => select("clone")} className="btn-brutal mt-auto" data-testid="cta-clone-chat">
              Browse clones →
            </button>
          </div>

          {/* Mood-Based Chat */}
          <div className="brutal-card p-7 flex flex-col" data-testid="card-mood-chat">
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="tag tag-violet">EMOTION-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("mood")} label="What is Mood-Based Chat?" testId="info-icon-mood" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">Mood-Based Chat</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              No persona, no setup. Type how you feel — the chat adapts its tone to match. Calm when you're stressed. Playful when you're playful.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-violet-soft mt-0.5">●</span> Detects emotional tone in real time</li>
              <li className="flex items-start gap-2"><span className="text-violet-soft mt-0.5">●</span> Adapts reply style + UI subtly</li>
              <li className="flex items-start gap-2"><span className="text-violet-soft mt-0.5">●</span> Built-in safety for distress signals</li>
            </ul>
            <button onClick={() => select("mood")} className="btn-violet mt-auto" data-testid="cta-mood-chat">
              Start mood chat →
            </button>
          </div>
        </div>
      </div>

      <ChatInfoModal
        open={openInfo === "clone"}
        onClose={() => setOpenInfo(null)}
        info={{ ...CHAT_INFO.clone, cta: { label: "Browse clones →", onClick: () => select("clone") } }}
      />
      <ChatInfoModal
        open={openInfo === "mood"}
        onClose={() => setOpenInfo(null)}
        info={{ ...CHAT_INFO.mood, cta: { label: "Start mood chat →", onClick: () => select("mood") } }}
      />
    </section>
  );
}
