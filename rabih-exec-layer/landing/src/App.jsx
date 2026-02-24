import { useEffect, useRef, useState } from 'react'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

// ── Design tokens ──────────────────────────────────────────────────────────
const C = {
  obsidian:  '#0D0D12',
  champagne: '#C9A84C',
  ivory:     '#FAF8F5',
  slate:     '#2A2A35',
}

// Real Unsplash images — dark marble / architectural / luxury
const HERO_IMG    = 'https://images.unsplash.com/photo-1557804506-669a67965ba0?w=1920&q=80&auto=format&fit=crop'
const TEXTURE_IMG = 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1920&q=80&auto=format&fit=crop'

// ── Navbar ─────────────────────────────────────────────────────────────────
function Navbar({ scrolled }) {
  return (
    <nav
      className="fixed top-4 left-1/2 z-50 flex items-center gap-6 px-6 py-3 rounded-full transition-all duration-500"
      style={{
        transform: 'translateX(-50%)',
        background: scrolled ? 'rgba(250,248,245,0.75)' : 'transparent',
        backdropFilter: scrolled ? 'blur(20px)' : 'none',
        border: scrolled ? '1px solid rgba(13,13,18,0.08)' : '1px solid transparent',
        boxShadow: scrolled ? '0 4px 24px rgba(0,0,0,0.08)' : 'none',
      }}
    >
      <span
        className="font-sans font-bold text-sm tracking-tight"
        style={{ color: scrolled ? C.obsidian : C.ivory }}
      >
        DriftZero
      </span>
      <div className="hidden md:flex items-center gap-1">
        {['Features', 'How it works', 'Pricing'].map(l => (
          <a
            key={l}
            href={`#${l.toLowerCase().replace(/ /g, '-')}`}
            className="nav-link px-3 py-1 text-sm font-medium"
            style={{ color: scrolled ? C.slate : 'rgba(250,248,245,0.75)', textDecoration: 'none' }}
          >
            {l}
          </a>
        ))}
      </div>
      <button
        className="btn-magnetic px-4 py-2 rounded-full text-sm font-semibold"
        style={{ background: C.champagne, color: C.obsidian }}
        onClick={() => document.getElementById('cta')?.scrollIntoView({ behavior: 'smooth' })}
      >
        <span className="relative z-10">Book a demo</span>
        <span className="btn-bg rounded-full" style={{ background: '#b8933a' }} />
      </button>
    </nav>
  )
}

// ── Hero ───────────────────────────────────────────────────────────────────
function Hero() {
  const ref = useRef(null)
  const heroRef = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.hero-line', {
        y: 40, opacity: 0, duration: 1.1, stagger: 0.08,
        ease: 'power3.out', delay: 0.2,
      })
      gsap.from('.hero-cta', {
        y: 24, opacity: 0, duration: 0.9, ease: 'power3.out', delay: 0.65,
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  return (
    <section
      id="hero"
      ref={heroRef}
      className="relative flex items-end"
      style={{ height: '100dvh', minHeight: '600px' }}
    >
      {/* Background image */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage: `url(${HERO_IMG})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      />
      {/* Gradient overlay */}
      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(to top, ${C.obsidian} 0%, rgba(13,13,18,0.7) 40%, rgba(13,13,18,0.2) 100%)`,
        }}
      />
      {/* Content */}
      <div ref={ref} className="relative z-10 px-8 pb-20 md:px-16 md:pb-28 max-w-4xl">
        <p className="hero-line font-mono text-sm tracking-widest uppercase mb-4"
           style={{ color: C.champagne }}>
          Execution Intelligence
        </p>
        <h1 className="hero-line font-sans font-black leading-none mb-2"
            style={{ fontSize: 'clamp(3rem, 8vw, 6.5rem)', color: C.ivory }}>
          Execution meets
        </h1>
        <h1 className="hero-line font-drama italic leading-none mb-8"
            style={{ fontSize: 'clamp(3.5rem, 10vw, 8rem)', color: C.champagne }}>
          Clarity.
        </h1>
        <p className="hero-line text-base md:text-lg max-w-lg mb-10 leading-relaxed"
           style={{ color: 'rgba(250,248,245,0.65)' }}>
          Stop letting decisions vanish into Slack threads. DriftZero captures
          every commitment, tracks every owner, and surfaces drift before it
          costs you.
        </p>
        <button
          className="hero-cta btn-magnetic px-8 py-4 rounded-full text-base font-semibold"
          style={{ background: C.champagne, color: C.obsidian }}
          onClick={() => document.getElementById('cta')?.scrollIntoView({ behavior: 'smooth' })}
        >
          <span className="relative z-10">Book a demo</span>
          <span className="btn-bg rounded-full" style={{ background: '#b8933a' }} />
        </button>
      </div>
    </section>
  )
}

// ── Feature Card 1: Diagnostic Shuffler ───────────────────────────────────
function ShufflerCard() {
  const labels = ['Decision logged', 'Owner assigned', 'Timestamp recorded']
  const [stack, setStack] = useState(labels)

  useEffect(() => {
    const id = setInterval(() => {
      setStack(prev => {
        const next = [...prev]
        next.unshift(next.pop())
        return next
      })
    }, 2800)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="rounded-[2rem] border p-8 flex flex-col gap-6 h-full"
         style={{ background: C.ivory, borderColor: 'rgba(13,13,18,0.08)', boxShadow: '0 8px 40px rgba(13,13,18,0.06)' }}>
      <div>
        <p className="font-mono text-xs tracking-widest uppercase mb-2" style={{ color: C.champagne }}>
          Auto-capture
        </p>
        <h3 className="font-sans font-bold text-xl" style={{ color: C.obsidian }}>
          Decisions captured automatically
        </h3>
        <p className="text-sm mt-2 leading-relaxed" style={{ color: C.slate }}>
          Every decision made in Slack — structured, attributed, timestamped.
        </p>
      </div>
      <div className="relative h-32 flex items-center justify-center">
        {stack.map((label, i) => (
          <div
            key={label}
            className="absolute w-full rounded-2xl px-5 py-3 font-sans text-sm font-medium border"
            style={{
              transform: `translateY(${i * 12}px) scale(${1 - i * 0.04})`,
              zIndex: 3 - i,
              opacity: 1 - i * 0.25,
              background: i === 0 ? C.obsidian : C.ivory,
              color: i === 0 ? C.champagne : C.slate,
              borderColor: i === 0 ? C.champagne : 'rgba(13,13,18,0.12)',
              transition: 'all 0.5s cubic-bezier(0.34,1.56,0.64,1)',
              boxShadow: '0 2px 12px rgba(13,13,18,0.08)',
            }}
          >
            ✓ {label}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Feature Card 2: Telemetry Typewriter ──────────────────────────────────
const FEED_MESSAGES = [
  'Action: Deploy staging env — owner: @mike',
  'Decision: Migrate auth to Cognito — confirmed',
  'Risk: Session tokens may break on cutover',
  'Action: Write migration runbook — owner: @sarah',
  'Decision: Launch date pushed to March 10',
]

function TypewriterCard() {
  const [lineIdx, setLineIdx] = useState(0)
  const [displayed, setDisplayed] = useState('')
  const [charIdx, setCharIdx] = useState(0)
  const [visible, setVisible] = useState([])

  useEffect(() => {
    const target = FEED_MESSAGES[lineIdx % FEED_MESSAGES.length]
    if (charIdx < target.length) {
      const t = setTimeout(() => {
        setDisplayed(target.slice(0, charIdx + 1))
        setCharIdx(c => c + 1)
      }, 30)
      return () => clearTimeout(t)
    } else {
      const t = setTimeout(() => {
        setVisible(v => [...v.slice(-3), target])
        setDisplayed('')
        setCharIdx(0)
        setLineIdx(l => l + 1)
      }, 1200)
      return () => clearTimeout(t)
    }
  }, [charIdx, lineIdx])

  return (
    <div className="rounded-[2rem] border p-8 flex flex-col gap-6 h-full"
         style={{ background: C.ivory, borderColor: 'rgba(13,13,18,0.08)', boxShadow: '0 8px 40px rgba(13,13,18,0.06)' }}>
      <div>
        <p className="font-mono text-xs tracking-widest uppercase mb-2" style={{ color: C.champagne }}>
          Action tracking
        </p>
        <h3 className="font-sans font-bold text-xl" style={{ color: C.obsidian }}>
          Actions tracked with owners
        </h3>
        <p className="text-sm mt-2 leading-relaxed" style={{ color: C.slate }}>
          Every commitment extracted and assigned — no manual entry required.
        </p>
      </div>
      <div className="rounded-2xl p-4 flex-1 overflow-hidden"
           style={{ background: C.obsidian, minHeight: '120px' }}>
        <div className="flex items-center gap-2 mb-3">
          <span className="pulse-dot w-2 h-2 rounded-full" style={{ background: '#22c55e' }} />
          <span className="font-mono text-xs tracking-widest" style={{ color: 'rgba(250,248,245,0.4)' }}>
            LIVE FEED
          </span>
        </div>
        <div className="space-y-1">
          {visible.map((line, i) => (
            <p key={i} className="font-mono text-xs leading-relaxed"
               style={{ color: 'rgba(250,248,245,0.45)' }}>
              {line}
            </p>
          ))}
          {displayed && (
            <p className="font-mono text-xs leading-relaxed" style={{ color: C.champagne }}>
              {displayed}
              <span className="cursor-blink ml-0.5" style={{ color: C.champagne }}>▌</span>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Feature Card 3: Cursor Protocol Scheduler ─────────────────────────────
const DAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

function SchedulerCard() {
  const [activeDay, setActiveDay] = useState(null)
  const [cursorPos, setCursorPos] = useState({ x: 20, y: 20 })
  const [phase, setPhase] = useState('idle')

  useEffect(() => {
    const steps = [
      () => { setPhase('moving'); setCursorPos({ x: 75, y: 60 }) },
      () => { setActiveDay(3); setPhase('pressed') },
      () => { setPhase('moving'); setCursorPos({ x: 75, y: 120 }) },
      () => { setPhase('saving') },
      () => { setPhase('done') },
      () => { setActiveDay(null); setCursorPos({ x: 20, y: 20 }); setPhase('idle') },
    ]
    let i = 0
    const run = () => {
      if (i < steps.length) { steps[i](); i++ }
      else i = 0
    }
    const id = setInterval(run, 700)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="rounded-[2rem] border p-8 flex flex-col gap-6 h-full"
         style={{ background: C.ivory, borderColor: 'rgba(13,13,18,0.08)', boxShadow: '0 8px 40px rgba(13,13,18,0.06)' }}>
      <div>
        <p className="font-mono text-xs tracking-widest uppercase mb-2" style={{ color: C.champagne }}>
          Drift detection
        </p>
        <h3 className="font-sans font-bold text-xl" style={{ color: C.obsidian }}>
          Drift detected before it costs you
        </h3>
        <p className="text-sm mt-2 leading-relaxed" style={{ color: C.slate }}>
          Commitments gone silent are flagged automatically — zero manual follow-up.
        </p>
      </div>
      <div className="rounded-2xl p-4 relative" style={{ background: C.obsidian }}>
        <div className="grid grid-cols-7 gap-1 mb-4">
          {DAYS.map((d, i) => (
            <div
              key={i}
              className="rounded-lg py-2 text-center font-mono text-xs transition-all duration-300"
              style={{
                background: activeDay === i ? C.champagne : 'rgba(250,248,245,0.06)',
                color: activeDay === i ? C.obsidian : 'rgba(250,248,245,0.4)',
                transform: activeDay === i && phase === 'pressed' ? 'scale(0.95)' : 'scale(1)',
              }}
            >
              {d}
            </div>
          ))}
        </div>
        <div className="flex justify-end">
          <div
            className="rounded-xl px-3 py-1.5 font-mono text-xs transition-all duration-300"
            style={{
              background: phase === 'saving' || phase === 'done' ? C.champagne : 'rgba(250,248,245,0.1)',
              color: phase === 'saving' || phase === 'done' ? C.obsidian : 'rgba(250,248,245,0.4)',
            }}
          >
            {phase === 'done' ? '✓ Saved' : 'Save'}
          </div>
        </div>
        {/* Animated SVG cursor */}
        <svg
          className="absolute pointer-events-none"
          style={{
            left: cursorPos.x,
            top: cursorPos.y,
            transition: 'left 0.5s ease, top 0.5s ease',
            opacity: phase === 'idle' ? 0 : 1,
          }}
          width="14" height="18" viewBox="0 0 14 18" fill="none"
        >
          <path d="M0 0L0 14L4 10L6 16L8 15L6 9L10 9L0 0Z"
                fill={C.champagne} stroke={C.obsidian} strokeWidth="0.5" />
        </svg>
      </div>
    </div>
  )
}

// ── Features section ───────────────────────────────────────────────────────
function Features() {
  return (
    <section id="features" className="py-24 px-6 md:px-16">
      <div className="max-w-6xl mx-auto">
        <p className="font-mono text-xs tracking-widest uppercase mb-3 text-center" style={{ color: C.champagne }}>
          What DriftZero does
        </p>
        <h2 className="font-sans font-black text-4xl md:text-5xl text-center mb-16 leading-tight"
            style={{ color: C.obsidian }}>
          Three ways we eliminate<br />execution debt
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <ShufflerCard />
          <TypewriterCard />
          <SchedulerCard />
        </div>
      </div>
    </section>
  )
}

// ── Philosophy ─────────────────────────────────────────────────────────────
function Philosophy() {
  const ref = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.manifesto-word', {
        y: 30, opacity: 0, duration: 0.7,
        stagger: 0.05, ease: 'power3.out',
        scrollTrigger: { trigger: ref.current, start: 'top 70%' },
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  return (
    <section
      ref={ref}
      className="relative py-32 px-6 md:px-16 overflow-hidden"
      style={{ background: C.obsidian }}
    >
      <div
        className="absolute inset-0 opacity-10"
        style={{
          backgroundImage: `url(${TEXTURE_IMG})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      />
      <div className="relative z-10 max-w-4xl mx-auto">
        <p className="font-sans text-lg mb-6" style={{ color: 'rgba(250,248,245,0.45)' }}>
          Most execution tools focus on:{' '}
          <span style={{ color: 'rgba(250,248,245,0.6)' }}>
            project boards, ticket trackers, and status updates nobody reads.
          </span>
        </p>
        <div className="text-4xl md:text-6xl font-drama italic leading-tight">
          {'We focus on: where decisions go to '.split(' ').map((word, i) => (
            <span key={i} className="manifesto-word inline-block mr-3" style={{ color: C.ivory }}>
              {word}
            </span>
          ))}
          <span className="manifesto-word inline-block font-drama italic"
                style={{ color: C.champagne }}>
            die.
          </span>
        </div>
      </div>
    </section>
  )
}

// ── Protocol: Sticky Stacking ──────────────────────────────────────────────
const STEPS = [
  {
    num: '01',
    title: 'Connect',
    desc: 'Install the DriftZero Slack bot in under 60 seconds. Add it to the channels that matter. No configuration required.',
    svg: (
      <svg width="120" height="120" viewBox="0 0 120 120" fill="none" className="slow-spin opacity-30">
        {[0,1,2,3].map(i => (
          <circle key={i} cx="60" cy="60" r={20 + i * 12}
                  stroke="#C9A84C" strokeWidth="1" strokeDasharray="4 4" />
        ))}
        <circle cx="60" cy="60" r="6" fill="#C9A84C" />
      </svg>
    ),
  },
  {
    num: '02',
    title: 'Extract',
    desc: 'Claude reads every thread and extracts decisions, actions, and risks with confidence scores. Zero false positives policy.',
    svg: (
      <svg width="120" height="100" viewBox="0 0 120 100" className="opacity-30 overflow-hidden">
        <defs>
          <clipPath id="scan-clip"><rect width="120" height="100" /></clipPath>
        </defs>
        {Array.from({ length: 7 }).map((_, r) =>
          Array.from({ length: 10 }).map((_, c) => (
            <circle key={`${r}-${c}`} cx={6 + c * 12} cy={8 + r * 13} r="2" fill="#C9A84C" />
          ))
        )}
        <rect className="laser-scan" x="-24" y="0" width="24" height="100"
              fill="url(#scan-grad)" clipPath="url(#scan-clip)" />
        <defs>
          <linearGradient id="scan-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#C9A84C" stopOpacity="0" />
            <stop offset="50%" stopColor="#C9A84C" stopOpacity="0.6" />
            <stop offset="100%" stopColor="#C9A84C" stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>
    ),
  },
  {
    num: '03',
    title: 'Converge',
    desc: 'Leadership gets a real-time view of every decision, action, and drifting commitment — across the entire org.',
    svg: (
      <svg width="180" height="60" viewBox="0 0 180 60" fill="none" className="opacity-30">
        <path
          className="ekg-path"
          d="M0 30 L30 30 L40 10 L50 50 L60 30 L80 30 L90 5 L100 55 L110 30 L130 30 L140 15 L150 45 L160 30 L180 30"
          stroke="#C9A84C" strokeWidth="2.5" fill="none"
          strokeDasharray="600" strokeDashoffset="600"
        />
      </svg>
    ),
  },
]

function Protocol() {
  const containerRef = useRef(null)

  useEffect(() => {
    const cards = gsap.utils.toArray('.stack-card')
    if (cards.length < 2) return

    const ctx = gsap.context(() => {
      cards.forEach((card, i) => {
        if (i === cards.length - 1) return
        ScrollTrigger.create({
          trigger: card,
          start: 'top top',
          end: 'bottom top',
          pin: true,
          pinSpacing: false,
          onUpdate: self => {
            const p = self.progress
            gsap.set(card, {
              scale: 1 - p * 0.08,
              filter: `blur(${p * 12}px)`,
              opacity: 1 - p * 0.45,
            })
          },
        })
      })
    }, containerRef)
    return () => ctx.revert()
  }, [])

  return (
    <section id="how-it-works" ref={containerRef}>
      {STEPS.map((step, i) => (
        <div
          key={step.num}
          className="stack-card flex items-center justify-center px-6 md:px-16"
          style={{
            height: '100vh',
            background: i % 2 === 0 ? C.obsidian : C.slate,
          }}
        >
          <div className="max-w-3xl w-full flex flex-col md:flex-row items-center gap-12">
            <div className="shrink-0">{step.svg}</div>
            <div>
              <span className="font-mono text-xs tracking-widest mb-3 block" style={{ color: C.champagne }}>
                {step.num}
              </span>
              <h2 className="font-sans font-black text-5xl md:text-7xl leading-none mb-6"
                  style={{ color: C.ivory }}>
                {step.title}
              </h2>
              <p className="text-base md:text-lg leading-relaxed max-w-lg"
                 style={{ color: 'rgba(250,248,245,0.6)' }}>
                {step.desc}
              </p>
            </div>
          </div>
        </div>
      ))}
    </section>
  )
}

// ── CTA ────────────────────────────────────────────────────────────────────
function CTA() {
  return (
    <section id="cta" className="py-32 px-6 text-center" style={{ background: C.ivory }}>
      <p className="font-mono text-xs tracking-widest uppercase mb-4" style={{ color: C.champagne }}>
        Get started
      </p>
      <h2 className="font-drama italic text-5xl md:text-7xl mb-6 leading-tight" style={{ color: C.obsidian }}>
        Zero drift.<br />Total clarity.
      </h2>
      <p className="text-lg max-w-lg mx-auto mb-10 leading-relaxed" style={{ color: C.slate }}>
        Install in 60 seconds. No workflow changes. Leadership gets visibility
        from day one.
      </p>
      <button
        className="btn-magnetic px-10 py-5 rounded-full text-base font-bold"
        style={{ background: C.obsidian, color: C.ivory }}
      >
        <span className="relative z-10">Book a demo</span>
        <span className="btn-bg rounded-full" style={{ background: C.slate }} />
      </button>
    </section>
  )
}

// ── Footer ─────────────────────────────────────────────────────────────────
function Footer() {
  return (
    <footer
      className="px-8 py-16 rounded-t-[4rem]"
      style={{ background: C.obsidian }}
    >
      <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-12">
        <div>
          <p className="font-sans font-bold text-lg mb-2" style={{ color: C.ivory }}>DriftZero</p>
          <p className="text-sm leading-relaxed" style={{ color: 'rgba(250,248,245,0.4)' }}>
            Eliminate execution gaps by turning<br />Slack conversations into accountability.
          </p>
          <div className="flex items-center gap-2 mt-6">
            <span className="op-dot w-2 h-2 rounded-full" style={{ background: '#22c55e' }} />
            <span className="font-mono text-xs" style={{ color: 'rgba(250,248,245,0.4)' }}>
              SYSTEM OPERATIONAL
            </span>
          </div>
        </div>
        <div>
          <p className="font-sans font-semibold text-xs uppercase tracking-widest mb-4"
             style={{ color: 'rgba(250,248,245,0.3)' }}>Product</p>
          {['Features', 'How it works', 'Pricing', 'Changelog'].map(l => (
            <a key={l} href="#" className="nav-link block text-sm mb-2"
               style={{ color: 'rgba(250,248,245,0.5)', textDecoration: 'none' }}>
              {l}
            </a>
          ))}
        </div>
        <div>
          <p className="font-sans font-semibold text-xs uppercase tracking-widest mb-4"
             style={{ color: 'rgba(250,248,245,0.3)' }}>Company</p>
          {['Privacy', 'Terms', 'Security', 'Contact'].map(l => (
            <a key={l} href="#" className="nav-link block text-sm mb-2"
               style={{ color: 'rgba(250,248,245,0.5)', textDecoration: 'none' }}>
              {l}
            </a>
          ))}
        </div>
      </div>
      <div className="max-w-6xl mx-auto mt-12 pt-6 border-t flex justify-between items-center"
           style={{ borderColor: 'rgba(250,248,245,0.08)' }}>
        <p className="font-mono text-xs" style={{ color: 'rgba(250,248,245,0.25)' }}>
          © 2026 DriftZero. All rights reserved.
        </p>
        <p className="font-mono text-xs" style={{ color: 'rgba(250,248,245,0.25)' }}>
          Built with Claude
        </p>
      </div>
    </footer>
  )
}

// ── App root ───────────────────────────────────────────────────────────────
export default function App() {
  const [scrolled, setScrolled] = useState(false)
  const heroRef = useRef(null)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => setScrolled(!entry.isIntersecting),
      { threshold: 0.1 }
    )
    const heroEl = document.getElementById('hero')
    if (heroEl) observer.observe(heroEl)
    return () => observer.disconnect()
  }, [])

  return (
    <>
      <Navbar scrolled={scrolled} />
      <Hero />
      <Features />
      <Philosophy />
      <Protocol />
      <CTA />
      <Footer />
    </>
  )
}
