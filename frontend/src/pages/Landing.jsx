import { Link } from "react-router-dom";
import Navbar from "../components/Navbar";

const CARDS = [
  { color: "bg-lemon", title: "Personality sliders", body: "Funny ↔ Serious. Direct ↔ Diplomatic. Tune them like a synth." },
  { color: "bg-lilac", title: "Memory engine", body: "Your clone remembers facts, preferences, and recurring people." },
  { color: "bg-mint", title: "Public share link", body: "cloneme.ai/your-name — drop it in your bio. Watch the chats roll in." },
  { color: "bg-bubblegum", title: "Always on, never sleeps", body: "Your AI version replies to friends 24/7. Without the social anxiety." },
];

export default function Landing() {
  return (
    <div className="min-h-screen bg-cream">
      <Navbar />

      {/* Hero */}
      <section className="relative overflow-hidden dotted-bg" data-testid="landing-hero">
        <div className="max-w-6xl mx-auto px-5 md:px-8 pt-16 pb-24 md:pt-24 md:pb-32">
          <div className="inline-flex items-center gap-2 mb-8 animate-fade-up" data-testid="hero-badge">
            <span className="tag bg-mint">v0.1 · Made with feelings</span>
            <span className="tag bg-lilac">No code. Just vibes.</span>
          </div>

          <h1 className="heading-display text-5xl sm:text-6xl md:text-7xl lg:text-8xl max-w-5xl animate-fade-up" data-testid="hero-headline">
            Make an <span className="bg-lemon px-3 -rotate-2 inline-block border-2 border-ink shadow-brutal-sm">AI version</span> of yourself.
            <br />
            Let people <span className="bg-bubblegum px-3 rotate-1 inline-block border-2 border-ink shadow-brutal-sm">talk to it.</span>
          </h1>

          <p className="mt-8 text-lg md:text-xl max-w-2xl text-foreground/80 font-medium animate-fade-up" style={{ animationDelay: "0.1s" }} data-testid="hero-subheadline">
            CloneMe AI builds a chatty digital twin of you — your tone, your humor, your weird opinions —
            so your friends, fans, or curious strangers can have a conversation when you can't.
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-3 animate-fade-up" style={{ animationDelay: "0.2s" }}>
            <Link to="/register" className="btn-brutal text-base" data-testid="hero-cta-primary">
              Build your clone — free →
            </Link>
            <Link to="/login" className="btn-ghost text-base" data-testid="hero-cta-secondary">
              I already have one
            </Link>
          </div>

          {/* Floating accent shapes */}
          <div className="hidden md:block absolute right-10 top-32 w-28 h-28 bg-mint border-2 border-ink rounded-3xl rotate-12 shadow-brutal-lg" aria-hidden />
          <div className="hidden md:block absolute right-44 top-72 w-16 h-16 bg-bubblegum border-2 border-ink rounded-full shadow-brutal" aria-hidden />
          <div className="hidden md:block absolute right-20 top-96 w-20 h-20 bg-lilac border-2 border-ink -rotate-6 shadow-brutal" aria-hidden />
        </div>
      </section>

      {/* Bento features */}
      <section className="border-t-2 border-ink bg-white" data-testid="features-section">
        <div className="max-w-6xl mx-auto px-5 md:px-8 py-16 md:py-24">
          <h2 className="heading-display text-3xl md:text-5xl mb-3" data-testid="features-heading">A clone that feels like you.</h2>
          <p className="text-muted-foreground max-w-2xl mb-10 font-medium">Not a generic chatbot pretending. A digital twin trained on your style, tuned to your rules, with memories you control.</p>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {CARDS.map((c, i) => (
              <div key={i} className={`brutal-card p-6 ${c.color}`} data-testid={`feature-card-${i}`}>
                <div className="font-mono text-xs mb-3">0{i + 1}</div>
                <h3 className="heading-display text-2xl mb-2">{c.title}</h3>
                <p className="font-medium text-sm">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="border-t-2 border-ink bg-cream" data-testid="how-it-works">
        <div className="max-w-6xl mx-auto px-5 md:px-8 py-16 md:py-24">
          <h2 className="heading-display text-3xl md:text-5xl">Three steps. Zero awkward DMs.</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mt-10">
            {[
              { n: "01", t: "Set up your identity", d: "Drop your name, bio, and the kind of conversations you actually want to have." },
              { n: "02", t: "Tune your personality", d: "Slide between funny and serious, short and long. Add catchphrases. Block topics." },
              { n: "03", t: "Share your link", d: "Paste cloneme.ai/your-name in your bio. Friends DM your AI. You sleep better." },
            ].map((s, i) => (
              <div key={i} className="brutal-card p-6" data-testid={`step-card-${i}`}>
                <div className="font-mono text-xs text-bubblegum mb-2">STEP {s.n}</div>
                <h3 className="heading-display text-2xl mb-2">{s.t}</h3>
                <p className="font-medium text-sm text-foreground/80">{s.d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="border-y-2 border-ink bg-lemon" data-testid="final-cta-section">
        <div className="max-w-4xl mx-auto px-5 md:px-8 py-20 text-center">
          <h2 className="heading-display text-4xl md:text-6xl">Your future self<br />is already typing.</h2>
          <p className="mt-5 text-lg font-medium max-w-xl mx-auto">Build your clone in 3 minutes. Share it forever.</p>
          <Link to="/register" className="btn-brutal mt-8 text-base bg-white" data-testid="final-cta">
            Start free →
          </Link>
        </div>
      </section>

      <footer className="bg-ink text-cream py-8" data-testid="footer">
        <div className="max-w-6xl mx-auto px-5 md:px-8 flex items-center justify-between text-xs font-mono uppercase tracking-widest">
          <span>© CloneMe AI</span>
          <span>Built with feelings · Not a real person</span>
        </div>
      </footer>
    </div>
  );
}
