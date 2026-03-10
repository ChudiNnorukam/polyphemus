"use client";

import { useEffect, useRef, useCallback } from "react";

const myths = [
  { num: "01", myth: "Users read your carefully crafted copy", verdict: "They don't." },
  { num: "02", myth: "5 users find 85% of usability problems", verdict: "Sometimes. Maybe." },
  { num: "03", myth: "More choices = happier users", verdict: "The opposite." },
  { num: "04", myth: "We'll fix the UX later", verdict: "100× more expensive." },
  { num: "05", myth: "Three clicks rule", verdict: "Never proven." },
];

const principles = [
  "Every claim needs a source",
  "One study is never enough",
  "Sample size matters more than headlines",
  "Your context is not their context",
];

export default function Home() {
  const glowRef = useRef<HTMLDivElement>(null);
  const glowPos = useRef({ x: 0, y: 0 });
  const currentPos = useRef({ x: 0, y: 0 });

  const animateGlow = useCallback(() => {
    currentPos.current.x += (glowPos.current.x - currentPos.current.x) * 0.06;
    currentPos.current.y += (glowPos.current.y - currentPos.current.y) * 0.06;
    if (glowRef.current) {
      glowRef.current.style.left = currentPos.current.x + "px";
      glowRef.current.style.top = currentPos.current.y + "px";
    }
    requestAnimationFrame(animateGlow);
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      glowPos.current = { x: e.clientX, y: e.clientY };
    };
    document.addEventListener("mousemove", handleMouseMove);
    const raf = requestAnimationFrame(animateGlow);

    if ("ontouchstart" in window && glowRef.current) {
      glowRef.current.style.display = "none";
    }

    const nav = document.querySelector("nav");
    const handleScroll = () => {
      if (nav) {
        if (window.scrollY > 50) {
          nav.classList.add("scrolled");
        } else {
          nav.classList.remove("scrolled");
        }
      }
    };
    window.addEventListener("scroll", handleScroll);

    const reveals = document.querySelectorAll(".reveal");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -60px 0px" }
    );
    reveals.forEach((el) => observer.observe(el));

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("scroll", handleScroll);
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [animateGlow]);

  return (
    <>
      {/* Glow */}
      <div ref={glowRef} className="glow" />

      {/* Grain */}
      <div className="grain" />

      {/* Nav */}
      <nav
        className="fixed top-0 left-0 right-0 px-10 py-6 flex justify-between items-center z-[1000] bg-transparent border-b border-transparent transition-all duration-400"
        style={{ transitionProperty: "background, border-color, padding" }}
      >
        <a href="#" className="flex items-center gap-2.5 no-underline text-white group">
          <div className="w-[38px] h-[38px] bg-white flex items-center justify-center font-[family-name:var(--font-bebas)] text-[1.4rem] text-[var(--color-bg)] transition-transform duration-300 group-hover:rotate-[-8deg] group-hover:scale-110">
            UX.
          </div>
          <span className="font-[family-name:var(--font-bebas)] text-[1.4rem] tracking-[2px]">
            UXGUY
          </span>
        </a>
        <a
          href="#subscribe"
          className="font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.15em] text-[var(--color-accent)] px-5 py-2.5 border border-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-[var(--color-bg)] transition-colors duration-300"
        >
          Get Notified
        </a>
      </nav>

      {/* Hero */}
      <section className="min-h-screen flex flex-col justify-center items-center p-8 relative text-center overflow-hidden">
        <div className="hero-ring" />
        <div className="hero-ring" />
        <div className="hero-ring" />

        <div className="hero-bg-lines">
          <span /><span /><span /><span /><span /><span /><span />
        </div>

        <div className="hero-corner hero-corner--tl" />
        <div className="hero-corner hero-corner--br" />

        <div className="relative z-[2]">
          <div
            className="inline-flex items-center gap-3 font-[family-name:var(--font-space-mono)] text-[0.7rem] uppercase tracking-[0.15em] text-[var(--color-text-dim)] mb-12 px-5 py-2.5 border border-[var(--color-border)] opacity-0"
            style={{ animation: "fadeIn 0.8s 0.3s forwards" }}
          >
            <span className="w-1.5 h-1.5 bg-[var(--color-accent)] rounded-full" style={{ animation: "pulseDot 2s ease-in-out infinite" }} />
            <span>Evidence-based UX myth busting</span>
          </div>

          <h1 className="font-[family-name:var(--font-bebas)] text-[clamp(3rem,8vw,8rem)] leading-[0.95] uppercase mb-8">
            <span className="block overflow-hidden">
              <span className="block" style={{ animation: "fadeIn 0.6s 0.6s forwards", opacity: 0 }}>
                YOU JUST
              </span>
            </span>
            <span className="block overflow-hidden">
              <span className="block" style={{ animation: "fadeIn 0.6s 0.8s forwards", opacity: 0 }}>
                <span className="glitch" data-text="PROVED">PROVED</span>
              </span>
            </span>
            <span className="block overflow-hidden">
              <span className="block text-[clamp(2rem,6vw,6rem)]" style={{ animation: "fadeIn 0.6s 1s forwards", opacity: 0 }}>
                OUR FIRST MYTH<span className="text-[var(--color-accent)]">.</span>
              </span>
            </span>
          </h1>

          <div
            className="max-w-md mx-auto border border-[var(--color-border-bright)] bg-[rgba(255,255,255,0.02)] p-8 opacity-0"
            style={{ animation: "fadeIn 0.8s 1.4s forwards" }}
          >
            <span className="block font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.15em] text-[var(--color-accent)] mb-4">
              Myth #01 — Exposed
            </span>
            <span className="block font-[family-name:var(--font-bebas)] text-[clamp(4rem,10vw,7rem)] leading-none text-white">
              79<span className="text-[var(--color-accent)] text-[0.5em]">%</span>
            </span>
            <span className="block font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.1em] text-[var(--color-text-dim)] mt-2">
              of users scan, not read. <strong className="text-white font-bold">You just did it.</strong>
            </span>
          </div>
        </div>

        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 text-center">
          <span className="block font-[family-name:var(--font-space-mono)] text-[0.6rem] uppercase tracking-[0.15em] text-[var(--color-text-muted)]">
            That was just the beginning
          </span>
          <div className="scroll-line" />
        </div>
      </section>

      {/* Truth / Myth List */}
      <section className="max-w-6xl mx-auto px-6 py-32" id="myths">
        {myths.map((m, i) => (
          <div
            key={m.num}
            className={`reveal ${i > 0 ? `reveal-delay-${Math.min(i, 4)}` : ""} flex flex-col md:flex-row md:items-center gap-4 md:gap-0 py-8 border-t border-[var(--color-border)]`}
          >
            <span className="font-[family-name:var(--font-space-mono)] text-[0.7rem] text-[var(--color-accent)] tracking-[0.1em] md:w-16 shrink-0">
              {m.num}
            </span>
            <span className="font-[family-name:var(--font-bebas)] text-[clamp(1.5rem,3.5vw,2.8rem)] leading-tight flex-1">
              <span className="myth-strike">&ldquo;{m.myth}&rdquo;</span>
            </span>
            <span className="font-[family-name:var(--font-space-mono)] text-[0.7rem] text-[var(--color-accent)] uppercase tracking-[0.1em] md:text-right md:w-48 shrink-0">
              {m.verdict}
            </span>
          </div>
        ))}
      </section>

      {/* Manifesto */}
      <section className="relative max-w-4xl mx-auto px-6 py-32 overflow-hidden">
        <div className="manifesto-bg-char">!</div>

        <div className="relative z-[2] border-l-2 border-[var(--color-accent)] pl-8 md:pl-12">
          <div className="reveal">
            <span className="font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.15em] text-[var(--color-accent)] block mb-8">
              Manifesto
            </span>
          </div>

          <div className="mb-12">
            <span className="reveal reveal-delay-1 block font-[family-name:var(--font-bebas)] text-[clamp(2rem,4vw,4rem)] leading-tight text-white/90">
              Popular ≠ true<span className="text-[var(--color-accent)]">.</span>
            </span>
            <span className="reveal reveal-delay-2 block font-[family-name:var(--font-bebas)] text-[clamp(2rem,4vw,4rem)] leading-tight text-white/90">
              Context kills universal rules<span className="text-[var(--color-accent)]">.</span>
            </span>
          </div>

          <div className="reveal space-y-6 text-[var(--color-text-dim)] leading-relaxed max-w-2xl">
            <p>
              A UX tip gets 10,000 retweets and suddenly it&apos;s a law.
              Nobody checks the original study. Nobody asks about sample size,
              industry, or whether it was even replicated.{" "}
              <strong className="text-white">Popularity is not peer review.</strong>
            </p>
            <p>
              And even when the research is solid — it was solid for{" "}
              <em>that</em> context. B2B enterprise software is not a food delivery app.
              A banking checkout is not an onboarding quiz.
              What works for Spotify will break your SaaS.{" "}
              <strong className="text-white">
                There are no universal answers in UX — only universal questions.
              </strong>
            </p>
            <p>
              We&apos;re not here to replace one set of rules with another.
              We&apos;re here to teach you how to read the evidence,
              understand the context, and make your own calls.
            </p>
            <p>
              <strong className="text-[var(--color-accent)]">
                If it can&apos;t survive scrutiny, it dies here.
              </strong>
            </p>
          </div>

          <div className="reveal mt-12 space-y-4">
            {principles.map((p, i) => (
              <div key={i} className="flex items-start gap-4">
                <span className="font-[family-name:var(--font-space-mono)] text-[0.7rem] text-[var(--color-accent)] tracking-[0.1em] mt-0.5">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="font-[family-name:var(--font-space-mono)] text-[0.75rem] text-[var(--color-text-dim)] tracking-[0.05em]">
                  {p}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Preview / Myth #01 */}
      <section className="max-w-5xl mx-auto px-6 py-32">
        <div className="reveal relative bg-[#0a0a0a] border border-[var(--color-border)] p-8 md:p-16 overflow-hidden">
          <div className="sample-watermark">01</div>

          <span className="relative z-[2] block font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.15em] text-[var(--color-accent)] mb-6">
            Preview / Myth #01
          </span>
          <h2 className="relative z-[2] font-[family-name:var(--font-bebas)] text-[clamp(1.8rem,4vw,3.5rem)] leading-tight text-white mb-8">
            &ldquo;USERS READ YOUR CAREFULLY CRAFTED COPY&rdquo;
          </h2>
          <blockquote className="relative z-[2] text-[var(--color-text-dim)] leading-relaxed max-w-2xl text-[0.95rem] mb-8">
            <strong className="text-white">79% scan. 16% read word-by-word.</strong>{" "}
            Your third paragraph? Statistically invisible.
            Nielsen proved it in 1997. Chartbeat confirmed it with 2 billion pageviews.
            25 years later, nothing changed.{" "}
            <strong className="text-white">Design for scanners or design for nobody.</strong>
          </blockquote>
          <div className="relative z-[2] flex items-center gap-3">
            <span
              className="w-1.5 h-1.5 bg-[var(--color-accent)] rounded-full"
              style={{ animation: "pulseDot 2s ease-in-out infinite" }}
            />
            <span className="font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
              Full deep-dive coming soon
            </span>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="max-w-3xl mx-auto px-6 py-32 text-center" id="subscribe">
        <div className="reveal">
          <span className="block font-[family-name:var(--font-space-mono)] text-[0.65rem] uppercase tracking-[0.15em] text-[var(--color-accent)] mb-6">
            Coming Soon
          </span>
          <h2 className="font-[family-name:var(--font-bebas)] text-[clamp(2.5rem,6vw,5rem)] leading-[0.95] mb-6">
            GET NOTIFIED<br />WHEN WE LAUNCH
          </h2>
          <p className="text-[var(--color-text-dim)] mb-10 max-w-md mx-auto">
            Zero fluff. Just the stuff that makes you rethink everything.
          </p>

          <form className="max-w-lg mx-auto" onSubmit={(e) => e.preventDefault()}>
            <div className="flex flex-col sm:flex-row gap-0">
              <input type="text" placeholder="First name" className="mc-input sm:border-r-0" />
              <input type="email" placeholder="your@email.com" className="mc-input" required />
            </div>

            <label className="flex items-start gap-3 mt-6 cursor-pointer text-left">
              <input type="checkbox" className="hidden peer" />
              <span className="mc-checkmark mt-0.5" />
              <span className="font-[family-name:var(--font-space-mono)] text-[0.6rem] text-[var(--color-text-muted)] leading-relaxed tracking-[0.02em]">
                I agree to receive emails from UXguy.io and consent to my data
                being processed for this purpose. No shady stuff — just myths,
                evidence, and the occasional &ldquo;told you so.&rdquo;{" "}
                <a href="#" className="text-[var(--color-text-dim)] underline underline-offset-2">
                  Privacy Policy
                </a>
              </span>
            </label>

            <button
              type="submit"
              className="w-full mt-6 bg-[var(--color-accent)] text-[var(--color-bg)] font-[family-name:var(--font-space-mono)] text-[0.7rem] uppercase tracking-[0.2em] py-4 font-bold hover:brightness-110 transition-all duration-300 cursor-pointer"
            >
              Notify Me
            </button>
          </form>

          <p className="mt-8 text-[var(--color-text-muted)] text-sm leading-relaxed max-w-md mx-auto">
            <strong className="text-[var(--color-text-dim)]">No spam. Ever.</strong>{" "}
            Only content that reopens your eyes to things you thought you already knew.
            The kind that makes you stop mid-scroll and say{" "}
            <em className="text-[var(--color-accent)]">&ldquo;wait, really?&rdquo;</em>
            <br /><br />
            <span className="text-[var(--color-text-muted)]">Unsubscribe anytime.</span>
          </p>
        </div>
      </section>

      {/* Footer */}
      <footer className="max-w-6xl mx-auto px-6">
        <div className="flex justify-between items-start py-12 border-t border-[var(--color-border)]">
          <div className="flex items-center gap-2.5">
            <div className="w-[38px] h-[38px] bg-white flex items-center justify-center font-[family-name:var(--font-bebas)] text-[1.4rem] text-[var(--color-bg)]">
              UX.
            </div>
            <span className="font-[family-name:var(--font-bebas)] text-[1.4rem] tracking-[2px]">
              UXGUY
            </span>
          </div>
          <div>
            <h4 className="font-[family-name:var(--font-space-mono)] text-[0.6rem] uppercase tracking-[0.15em] text-[var(--color-text-dim)] mb-3">
              Legal
            </h4>
            <a href="#" className="font-[family-name:var(--font-space-mono)] text-[0.65rem] text-[var(--color-text-muted)] hover:text-white transition-colors">
              Privacy
            </a>
          </div>
        </div>
        <div className="flex justify-between items-center py-6 border-t border-[var(--color-border)]">
          <span className="font-[family-name:var(--font-space-mono)] text-[0.55rem] uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
            © 2025 UXGUY.IO — EUROPE / QUESTIONING EVERYTHING
          </span>
          <button
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            className="font-[family-name:var(--font-space-mono)] text-[0.6rem] uppercase tracking-[0.1em] text-[var(--color-text-muted)] hover:text-white transition-colors bg-transparent border-none cursor-pointer"
          >
            Back to top ↑
          </button>
        </div>
      </footer>

      <style jsx global>{`
        nav.scrolled {
          background: rgba(3, 3, 3, 0.92) !important;
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border-bottom-color: var(--color-border) !important;
          padding-top: 1rem !important;
          padding-bottom: 1rem !important;
        }
      `}</style>
    </>
  );
}
