/**
 * Founder About section — premium, cinematic, dark-theme matched.
 * Mounted between the Hero and Features sections on Landing.
 *
 * Behavior:
 * - Desktop: portrait left, copy right
 * - Mobile: portrait centered on top, copy below
 * - Image lazy-loads, no CLS (fixed aspect ratio + width/height attrs)
 * - Soft amber glow ring + slow pulse animation
 * - Body copy is fade-up on scroll via existing animate-fade-up keyframe
 */
export default function FounderAboutSection() {
  return (
    <section
      className="border-t border-white/5 relative overflow-hidden"
      data-testid="founder-about-section"
      aria-labelledby="founder-heading"
    >
      {/* Decorative glow */}
      <div className="absolute -top-20 left-1/2 -translate-x-1/2 w-[680px] h-[680px] rounded-full bg-amber/5 blur-3xl pointer-events-none" aria-hidden />

      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-16 md:py-24 relative">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-10 md:gap-12 items-center">
          {/* Portrait */}
          <div className="md:col-span-5 flex justify-center md:justify-start">
            <div className="relative animate-fade-up">
              {/* Pulsing glow ring */}
              <div
                className="absolute inset-0 rounded-full bg-gradient-to-tr from-amber/45 via-amber/15 to-transparent blur-2xl scale-110 animate-glow-pulse"
                aria-hidden
              />
              <div className="absolute inset-0 rounded-full ring-2 ring-amber/35" aria-hidden />
              <img
                src="/founder.jpg"
                alt="Raja Praveen Katta — Founder of aiclonechats.com"
                width={280}
                height={280}
                loading="lazy"
                decoding="async"
                className="relative w-[220px] h-[220px] sm:w-[260px] sm:h-[260px] md:w-[300px] md:h-[300px] rounded-full object-cover object-center shadow-glow-amber border border-white/10 animate-float"
                data-testid="founder-portrait"
              />
            </div>
          </div>

          {/* Copy */}
          <div className="md:col-span-7 animate-fade-up" style={{ animationDelay: "0.12s" }}>
            <span className="tag tag-amber mb-4 inline-block">FOUNDER</span>
            <h2
              id="founder-heading"
              className="heading-display text-3xl sm:text-4xl md:text-5xl mb-5"
              data-testid="founder-heading"
            >
              About the Founder
            </h2>
            <div className="space-y-4 max-w-xl text-ink/80 font-medium leading-relaxed text-base sm:text-lg">
              <p>
                With the success of <span className="text-amber-soft font-semibold">Visionary Suite</span>, entrepreneur, AI innovator, and software architect <span className="text-ink font-semibold">Raja Praveen Katta</span> proudly introduces <span className="text-ink font-semibold">AI Clone Chats</span> — a next-generation AI platform designed to make conversations more human, interactive, and emotionally intelligent.
              </p>
              <p>
                Built with a vision to redefine digital interaction, AI Clone Chats enables users worldwide to experience unique AI-powered personalities, meaningful conversations, and creative chat experiences across multiple categories.
              </p>
              <p>
                Driven by innovation, storytelling, and advanced AI technology, Raja Praveen Katta continues building products that push the boundaries between humans and intelligent digital experiences.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
