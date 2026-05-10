import { Link } from "react-router-dom";
import Navbar from "../components/Navbar";
import ChatTypeCards from "../components/ChatTypeCards";
import FounderAboutSection from "../components/FounderAboutSection";

const FEATURES = [
  { tone: "amber", n: "01", title: "Personality engine", body: "Tune humor, directness, warmth, energy. Add catchphrases. Block topics. Your clone, your rules." },
  { tone: "violet", n: "02", title: "Memory you control", body: "Drop in facts, preferences, relationships. Edit, hide, or delete anything — anytime." },
  { tone: "emerald", n: "03", title: "One link to share", body: "aiclonechats.com/your-name. Drop it in your bio. Watch the chats roll in while you sleep." },
  { tone: "rose", n: "04", title: "Always on, never tired", body: "Your AI version replies 24/7. With your tone, your weird opinions — and zero social anxiety." },
];

export default function Landing() {
  return (
    <div className="page-bg">
      <Navbar />

      {/* Decorative orbs */}
      <div className="orb orb-amber w-[420px] h-[420px] -top-20 -right-20 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[480px] h-[480px] top-40 -left-32 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      {/* Hero */}
      <section className="relative" data-testid="landing-hero">
        <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 pt-10 pb-16 sm:pt-16 sm:pb-24 md:pt-24 md:pb-32">
          <div className="inline-flex flex-wrap items-center gap-2 mb-7 animate-fade-up" data-testid="hero-badge">
            <span className="tag tag-violet">v0.1 · Made with feelings</span>
            <span className="tag">Now in beta</span>
          </div>

          <h1 className="heading-display text-[2.25rem] sm:text-5xl md:text-6xl lg:text-7xl xl:text-[5.5rem] max-w-5xl animate-fade-up leading-[1.05]" data-testid="hero-headline">
            Make an <span className="bg-gradient-to-r from-amber to-amber-soft bg-clip-text text-transparent">AI version</span> of yourself.{" "}
            <br />
            Let people <span className="bg-gradient-to-r from-violet-soft to-violet bg-clip-text text-transparent">talk to it.</span>
          </h1>

          <p className="mt-5 sm:mt-7 text-base sm:text-lg md:text-xl max-w-2xl text-ink/75 font-medium animate-fade-up leading-relaxed" style={{ animationDelay: "0.1s" }} data-testid="hero-subheadline">
            aiclonechats.com builds a chatty digital twin of you — your tone, your humor, your weird opinions —
            so your friends, fans, or curious strangers can chat when you can't.
          </p>

          <div className="mt-8 sm:mt-10 flex flex-wrap items-center gap-3 animate-fade-up" style={{ animationDelay: "0.2s" }}>
            <Link to="/register" className="btn-brutal text-base" data-testid="hero-cta-primary">
              Build your clone — free →
            </Link>
            <Link to="/login" className="btn-ghost text-base" data-testid="hero-cta-secondary">
              I already have one
            </Link>
          </div>

          {/* Hero proof — chat preview card */}
          <div className="mt-16 grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-7 glass-card p-6 md:p-8 animate-pop-in">
              <div className="flex items-center gap-3 mb-5">
                <div className="w-11 h-11 rounded-full bg-gradient-to-br from-amber to-violet flex items-center justify-center font-display font-black text-bg text-lg">R</div>
                <div>
                  <div className="font-display font-bold text-lg flex items-center gap-2">
                    Raja AI <span className="tag tag-amber">AI Clone</span>
                  </div>
                  <div className="font-mono text-xs text-muted">aiclonechats.com/raja</div>
                </div>
              </div>
              <div className="space-y-3">
                <div className="flex justify-end">
                  <div className="chat-bubble-visitor text-sm">Should I quit my job and start that thing?</div>
                </div>
                <div className="flex justify-start">
                  <div className="chat-bubble-clone text-sm">Don't quit on a feeling. Quit on a signal. Build it on weekends until people pay you twice.</div>
                </div>
                <div className="flex justify-end">
                  <div className="chat-bubble-visitor text-sm">savage 😅</div>
                </div>
                <div className="flex justify-start">
                  <div className="chat-bubble-clone text-sm">It's free advice and I'm an AI clone. The savage part is included.</div>
                </div>
              </div>
            </div>

            <div className="lg:col-span-5 grid grid-cols-1 gap-6">
              <div className="glass-card p-6 animate-pop-in" style={{ animationDelay: ".1s" }}>
                <div className="flex items-start justify-between mb-3">
                  <div className="text-xs font-mono uppercase tracking-widest text-muted">Personality</div>
                  <span className="tag tag-amber">Live</span>
                </div>
                <div className="space-y-3">
                  {[["Humor", 7], ["Directness", 9], ["Warmth", 5]].map(([k, v]) => (
                    <div key={k}>
                      <div className="flex justify-between text-xs mb-1.5"><span className="font-medium">{k}</span><span className="font-mono">{v}/10</span></div>
                      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                        <div className="h-full bg-gradient-to-r from-amber to-amber-soft" style={{ width: `${(v) * 10}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="glass-card p-6 animate-pop-in" style={{ animationDelay: ".2s" }}>
                <div className="text-xs font-mono uppercase tracking-widest text-muted mb-3">Long-term memory</div>
                <div className="space-y-2">
                  {["Prefers blunt feedback", "Believes in shipping over polish", "Does not give legal advice"].map((t) => (
                    <div key={t} className="flex items-start gap-2 text-sm">
                      <span className="text-amber mt-0.5">●</span>
                      <span className="text-ink/85">{t}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* About the Founder */}
      <FounderAboutSection />

      {/* Two ways to chat */}
      <ChatTypeCards />

      {/* Bento features */}
      <section className="border-t border-white/5" data-testid="features-section">
        <div className="max-w-6xl mx-auto px-5 md:px-8 py-20 md:py-28">
          <div className="max-w-2xl mb-12">
            <span className="tag mb-4 inline-block">Why aiclonechats.com</span>
            <h2 className="heading-display text-3xl md:text-5xl mb-4" data-testid="features-heading">A clone that feels like you.</h2>
            <p className="text-muted max-w-2xl font-medium leading-relaxed">Not a generic chatbot pretending. A digital twin trained on your style, tuned to your rules, with memories you control.</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {FEATURES.map((c, i) => (
              <div key={i} className="brutal-card p-7 group" data-testid={`feature-card-${i}`}>
                <div className={`font-mono text-xs mb-4 tag tag-${c.tone}`}>{c.n}</div>
                <h3 className="heading-display text-2xl mb-2.5">{c.title}</h3>
                <p className="font-medium text-sm text-ink/70 leading-relaxed">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="border-t border-white/5 relative" data-testid="how-it-works">
        <div className="orb orb-violet w-[400px] h-[400px] top-20 right-0 opacity-30 animate-orb" aria-hidden />
        <div className="max-w-6xl mx-auto px-5 md:px-8 py-20 md:py-28">
          <h2 className="heading-display text-3xl md:text-5xl mb-12 max-w-2xl">Three steps. Zero awkward DMs.</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {[
              { n: "01", t: "Set up your identity", d: "Drop your name, bio, and the kind of conversations you actually want to have.", color: "tag-amber" },
              { n: "02", t: "Tune your personality", d: "Slide between funny and serious, short and long. Add catchphrases. Block topics.", color: "tag-violet" },
              { n: "03", t: "Share your link", d: "Paste aiclonechats.com/your-name in your bio. Friends DM your AI. You sleep better.", color: "tag-emerald" },
            ].map((s, i) => (
              <div key={i} className="brutal-card p-7" data-testid={`step-card-${i}`}>
                <div className={`tag ${s.color} mb-4 inline-block`}>STEP {s.n}</div>
                <h3 className="heading-display text-2xl mb-2.5">{s.t}</h3>
                <p className="font-medium text-sm text-ink/70 leading-relaxed">{s.d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="border-t border-white/5 relative overflow-hidden" data-testid="final-cta-section">
        <div className="orb orb-amber w-[500px] h-[500px] bottom-[-200px] left-1/2 -translate-x-1/2 opacity-40 animate-orb" aria-hidden />
        <div className="max-w-4xl mx-auto px-5 md:px-8 py-24 md:py-32 text-center relative">
          <h2 className="heading-display text-4xl md:text-6xl">Your future self<br /><span className="bg-gradient-to-r from-amber via-amber-soft to-violet-soft bg-clip-text text-transparent">is already typing.</span></h2>
          <p className="mt-5 text-lg font-medium max-w-xl mx-auto text-ink/70">Build your clone in 3 minutes. Share it forever.</p>
          <Link to="/register" className="btn-brutal mt-9 text-base" data-testid="final-cta">
            Start free →
          </Link>
        </div>
      </section>

      <footer className="border-t border-white/5 py-8" data-testid="footer">
        <div className="max-w-6xl mx-auto px-5 md:px-8 flex items-center justify-between text-xs font-mono uppercase tracking-widest text-muted">
          <span>© aiclonechats.com</span>
          <span>Built with feelings · Not a real person</span>
        </div>
      </footer>
    </div>
  );
}
