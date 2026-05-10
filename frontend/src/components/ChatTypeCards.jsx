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
  smart: {
    id: "smart",
    kicker: "OPTION 03",
    title: "What is Smart Reply?",
    body:
      "Smart Reply turns a real message you received into 3 copy-ready replies. Pick a mode (dating, professional, apology, negotiation), pick a tone, and we write the safe, warm, and confident version of what you should send.",
    how_to: [
      "Paste the message you got.",
      "Pick a mode — dating, professional, apology, or negotiation.",
      "Choose a tone (warm, calm, flirty, professional, confident, direct).",
      "Optionally add context (relationship, goal, what you want to say).",
      "Get 3 reply variants — short / medium / long. Tap Copy to send.",
    ],
    example: {
      input: "Hey, are we still on for Friday? Or have plans changed?",
      output: "Still on from my end — let me know if anything's changed for you.",
    },
    safety:
      "Smart Reply blocks harassment, manipulation, coercive dating advice, sexual pressure, fake threats, and revenge messages. If your input is unsafe, you'll get a healthier alternative instead.",
  },
  voice: {
    id: "voice",
    kicker: "OPTION 04",
    title: "What is Voice-First AI Messaging?",
    body:
      "Speak it, upload a voice note, or paste rough text. We clean up the filler, fix the grammar, and write 6 polished versions you can send right now — concise, professional, friendly, apology, dating, and negotiation.",
    how_to: [
      "Tap to record, upload a voice note, or paste messy text.",
      "We transcribe and clean up the speech in-memory (audio is never stored).",
      "Edit the cleaned input if anything's off.",
      "Get 6 tone-matched messages generated in parallel.",
      "One-tap refines: shorter, more confident, more polite, more flirty, more professional.",
    ],
    example: {
      input: "um yeah so like, tell my boss i will be like 30 minutes late because of traffic",
      output: "Hey — running about 30 minutes behind because of traffic. I'll be there as soon as I can.",
    },
    safety:
      "Audio is transcribed in memory and never persisted. Public share links are off by default and require explicit confirmation; phone numbers, emails, OTPs, addresses, and links are auto-redacted before any share is created.",
  },
  anonymous: {
    id: "anonymous",
    kicker: "OPTION 05",
    title: "What is Anonymous Reality?",
    body:
      "Topic-based anonymous rooms where strangers talk honestly without identity pressure, fake flexing, or toxicity. AI moderation keeps every room emotionally safe so you can say what you actually feel — and be heard, not judged.",
    how_to: [
      "Enter anonymously — no email, no name, no profile.",
      "Pick a room: Loneliness, Family Pressure, Money Reality, Relationships, Mental Load, and more.",
      "Talk honestly. The AI blocks toxicity and harassment before any message goes public.",
      "Self-harm content is met with supportive responses, never shaming.",
      "Report what feels off. Admins keep small healthy rooms over large chaotic ones.",
    ],
    example: {
      input: "I'm exhausted and I haven't told anyone.",
      output: "(another anonymous handle) I felt that exact thing last week. You're not alone in it.",
    },
    safety:
      "Built on a different kind of trust: no public profiles, no followers, no likes, no leaderboards. Just rooms where honesty is the only currency.",
  },
  debate: {
    id: "debate",
    kicker: "OPTION 06 · COMING SOON",
    title: "What is AI Debate Rooms?",
    body:
      "Live debate rooms where you pick a side and argue for or against a topic. AI scores your logic, clarity, evidence, and rebuttal strength in real time; the crowd votes; the ranking updates live; winners get a shareable result card.",
    how_to: [
      "Browse trending debate topics or create your own.",
      "Pick a side: for, against, or spectate.",
      "Submit your argument — moderation runs before broadcast.",
      "AI scores you across 5 dimensions: logic, clarity, evidence, rebuttal, civility.",
      "Crowd votes layer on top. Live ranking shifts in real time. Winner gets a shareable badge.",
    ],
    example: {
      input: "Topic: Should phones be banned in classrooms?",
      output: "Debate ends → winner card with your top argument, AI score, and crowd vote breakdown — copy link, share anywhere.",
    },
    safety:
      "Strong debate against ideas, never against people. Personal attacks, harassment, and hate are blocked before broadcast. Civility is part of the score.",
  },
};

export default function ChatTypeCards() {
  const navigate = useNavigate();
  const [openInfo, setOpenInfo] = useState(null);

  useEffect(() => {
    api.post("/analytics/event", { event_name: "chat_type_card_viewed" }).catch(() => {});
  }, []);

  const select = (chat_type) => {
    api.post("/analytics/event", { event_name: "chat_type_selected", metadata: { chat_type } }).catch(() => {});
    if (chat_type === "clone") {
      api.post("/analytics/event", { event_name: "clone_chat_started", metadata: { source: "chat_type_cards" } }).catch(() => {});
      navigate("/explore");
    } else if (chat_type === "mood") {
      api.post("/analytics/event", { event_name: "mood_chat_started", metadata: { source: "chat_type_cards" } }).catch(() => {});
      navigate("/mood-chat");
    } else if (chat_type === "smart") {
      api.post("/analytics/event", {
        event_name: "smart_reply_landing_view",
        metadata: { source: "chat_type_cards", experience_variant: "smart_reply_v1" },
      }).catch(() => {});
      navigate("/smart-reply");
    } else if (chat_type === "voice") {
      // Funnel separation: voice events live on voice_usage_events, NOT clone_analytics.
      api.post("/voice/track", { event_name: "voice_page_viewed" }).catch(() => {});
      navigate("/voice");
    } else if (chat_type === "anonymous") {
      api.post("/analytics/event", {
        event_name: "anonymous_reality_card_clicked",
        metadata: { source: "chat_type_cards" },
      }).catch(() => {});
      navigate("/anonymous-reality");
    } else if (chat_type === "debate") {
      // Coming soon — separate Emergent project (not built; demand-signal capture only).
      api.post("/analytics/event", {
        event_name: "ai_debate_rooms_interest_clicked",
        metadata: { source: "chat_type_cards", state: "coming_soon" },
      }).catch(() => {});
      setOpenInfo("debate");
    }
  };

  return (
    <section className="border-t border-white/5" data-testid="chat-type-cards-section">
      <div className="max-w-6xl mx-auto px-5 md:px-8 py-16 md:py-24">
        <div className="max-w-2xl mb-10">
          <span className="tag mb-4 inline-block">Pick your tool</span>
          <h2 className="heading-display text-3xl md:text-5xl mb-3">Six ways to talk.</h2>
          <p className="text-muted font-medium leading-relaxed">
            Personality-first, emotion-first, paste-and-reply, speak-it-clean — and soon, anonymous topic rooms and live AI-judged debates. Pick the one that fits the moment.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-5">
          {/* AI Clone Chat */}
          <div className="brutal-card p-7 flex flex-col" data-testid="card-clone-chat">
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="tag tag-amber">PERSONALITY-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("clone")} label="What is AI Clone Chat?" testId="info-icon-clone" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">AI Clone Chat</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              Chat with a real person's AI clone — their tone, humor, opinions, and weird takes.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Distinct personality per clone</li>
              <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Bio, tone, memories</li>
              <li className="flex items-start gap-2"><span className="text-amber mt-0.5">●</span> Public share link</li>
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
              No persona, no setup. Type how you feel — the chat adapts its tone to match.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-violet-soft mt-0.5">●</span> Detects emotional tone</li>
              <li className="flex items-start gap-2"><span className="text-violet-soft mt-0.5">●</span> Adapts reply style + UI</li>
              <li className="flex items-start gap-2"><span className="text-violet-soft mt-0.5">●</span> Built-in distress safety</li>
            </ul>
            <button onClick={() => select("mood")} className="btn-violet mt-auto" data-testid="cta-mood-chat">
              Start mood chat →
            </button>
          </div>

          {/* Smart Reply */}
          <div className="brutal-card p-7 flex flex-col" data-testid="card-smart-reply">
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="tag tag-emerald">UTILITY-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("smart")} label="What is Smart Reply?" testId="info-icon-smart" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">Smart Reply</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              Paste the message. Pick the mode. Get 3 copy-ready replies. Send the right one.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-emerald-soft mt-0.5">●</span> Dating · pro · apology · negotiation</li>
              <li className="flex items-start gap-2"><span className="text-emerald-soft mt-0.5">●</span> 3 lengths: short / medium / long</li>
              <li className="flex items-start gap-2"><span className="text-emerald-soft mt-0.5">●</span> Tone control + risk warnings</li>
            </ul>
            <button onClick={() => select("smart")} className="btn-brutal mt-auto" data-testid="cta-smart-reply">
              Open Smart Reply →
            </button>
          </div>

          {/* Voice-First AI Messaging */}
          <div className="brutal-card p-7 flex flex-col" data-testid="card-voice">
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="tag tag-emerald">VOICE-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("voice")} label="What is Voice-First AI Messaging?" testId="info-icon-voice" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">Voice → Message</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              Speak it, upload a voice note, or paste rough text. We write the polished version.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-emerald-soft mt-0.5">●</span> Record · upload · paste text</li>
              <li className="flex items-start gap-2"><span className="text-emerald-soft mt-0.5">●</span> 6 tones generated in parallel</li>
              <li className="flex items-start gap-2"><span className="text-emerald-soft mt-0.5">●</span> Audio never stored · 3 free trials</li>
            </ul>
            <button onClick={() => select("voice")} className="btn-brutal mt-auto" data-testid="cta-voice">
              Try Voice →
            </button>
          </div>

          {/* Anonymous Reality (live) */}
          <div className="brutal-card p-7 flex flex-col" data-testid="card-anonymous">
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="tag tag-rose">ANONYMOUS-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("anonymous")} label="What is Anonymous Reality?" testId="info-icon-anonymous" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">Anonymous Reality</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              Topic rooms where strangers talk honestly. No names. No fake flexing. AI moderation keeps it safe.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-rose-300 mt-0.5">●</span> Anonymous handles · no profile</li>
              <li className="flex items-start gap-2"><span className="text-rose-300 mt-0.5">●</span> Real-time topic-based rooms</li>
              <li className="flex items-start gap-2"><span className="text-rose-300 mt-0.5">●</span> Toxicity blocked before broadcast</li>
            </ul>
            <button onClick={() => select("anonymous")} className="btn-brutal mt-auto" data-testid="cta-anonymous">
              Enter anonymously →
            </button>
          </div>

          {/* AI Debate Rooms (coming soon) */}
          <div className="brutal-card p-7 flex flex-col relative overflow-hidden" data-testid="card-debate">
            <div className="absolute top-3 right-3 text-[10px] font-mono uppercase tracking-widest text-sky-300/80 bg-sky-500/10 border border-sky-400/30 rounded-full px-2 py-0.5" data-testid="debate-coming-soon-badge">
              Coming soon
            </div>
            <div className="flex items-center gap-3 mb-2">
              <span className="tag tag-sky">DEBATE-FIRST</span>
              <InfoIcon onClick={() => setOpenInfo("debate")} label="What is AI Debate Rooms?" testId="info-icon-debate" />
            </div>
            <h3 className="heading-display text-2xl md:text-3xl mb-3">AI Debate Rooms</h3>
            <p className="text-sm font-medium text-ink/70 leading-relaxed mb-5">
              Pick a side. Argue live. AI scores logic + clarity, the crowd votes, ranking shifts in real time.
            </p>
            <ul className="space-y-1.5 text-xs text-ink/70 mb-7">
              <li className="flex items-start gap-2"><span className="text-sky-300 mt-0.5">●</span> For / against / spectate · live rooms</li>
              <li className="flex items-start gap-2"><span className="text-sky-300 mt-0.5">●</span> AI scoring + crowd voting</li>
              <li className="flex items-start gap-2"><span className="text-sky-300 mt-0.5">●</span> Real-time ranking · shareable wins</li>
            </ul>
            <button onClick={() => select("debate")} className="btn-ghost mt-auto" data-testid="cta-debate">
              Learn more →
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
      <ChatInfoModal
        open={openInfo === "smart"}
        onClose={() => setOpenInfo(null)}
        info={{ ...CHAT_INFO.smart, cta: { label: "Open Smart Reply →", onClick: () => select("smart") } }}
      />
      <ChatInfoModal
        open={openInfo === "voice"}
        onClose={() => setOpenInfo(null)}
        info={{ ...CHAT_INFO.voice, cta: { label: "Try Voice →", onClick: () => select("voice") } }}
      />
      <ChatInfoModal
        open={openInfo === "anonymous"}
        onClose={() => setOpenInfo(null)}
        info={{
          ...CHAT_INFO.anonymous,
          cta: { label: "Enter anonymously →", onClick: () => select("anonymous") },
        }}
      />
      <ChatInfoModal
        open={openInfo === "debate"}
        onClose={() => setOpenInfo(null)}
        info={{
          ...CHAT_INFO.debate,
          cta: {
            label: "Coming soon",
            onClick: () => {
              api.post("/analytics/event", {
                event_name: "ai_debate_rooms_interest_clicked",
                metadata: { source: "info_modal_cta", state: "coming_soon" },
              }).catch(() => {});
              setOpenInfo(null);
            },
          },
        }}
      />
    </section>
  );
}
