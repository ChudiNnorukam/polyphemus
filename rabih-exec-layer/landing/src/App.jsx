import { useEffect, useRef, useState } from 'react'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
gsap.registerPlugin(ScrollTrigger)

const MOSS     = '#2E4036'
const CLAY     = '#CC5833'
const CREAM    = '#F2F0E9'
const CHARCOAL = '#1A1A1A'

/* ── NAVBAR ── */
function Navbar() {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const hero = document.getElementById('hero')
    if (!hero) return
    const obs = new IntersectionObserver(([e]) => setScrolled(!e.isIntersecting), { threshold: 0.1 })
    obs.observe(hero)
    return () => obs.disconnect()
  }, [])
  return (
    <nav className="fixed top-4 left-1/2 z-50 -translate-x-1/2 flex items-center gap-6 px-6 py-3 rounded-full transition-all duration-500"
      style={{
        background: scrolled ? 'rgba(242,240,233,0.88)' : 'transparent',
        backdropFilter: scrolled ? 'blur(20px)' : 'none',
        boxShadow: scrolled ? '0 2px 32px rgba(46,64,54,0.12)' : 'none',
        border: `1px solid ${scrolled ? 'rgba(46,64,54,0.12)' : 'transparent'}`,
      }}>
      <span className="font-bold text-base tracking-tight" style={{ fontFamily: 'var(--font-head)', color: scrolled ? MOSS : CREAM }}>
        Lucider AI
      </span>
      <div className="hidden md:flex items-center gap-5 text-sm font-medium" style={{ color: scrolled ? CHARCOAL : 'rgba(242,240,233,0.85)' }}>
        {['Features','How it works','Pricing'].map(l => (
          <a key={l} href="#" className="transition-transform hover:-translate-y-px">{l}</a>
        ))}
      </div>
      <a href="#" className="btn-magnetic text-sm font-semibold px-4 py-2 rounded-full" style={{ background: CLAY, color: CREAM }}>
        <span className="btn-bg rounded-full" style={{ background: MOSS }} />
        <span className="relative z-10">Request a demo</span>
      </a>
    </nav>
  )
}

/* ── HERO ── */
function Hero() {
  const ref = useRef(null)
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.hero-item', { y: 40, opacity: 0, duration: 1.1, stagger: 0.08, ease: 'power3.out', delay: 0.3 })
    }, ref)
    return () => ctx.revert()
  }, [])
  return (
    <section id="hero" ref={ref} className="relative w-full flex items-end pb-20 md:pb-28" style={{ height: '100dvh' }}>
      <img src="https://images.unsplash.com/photo-1518173946687-a4c8892bbd9f?w=1920&q=80&auto=format&fit=crop"
        alt="Dark forest" className="absolute inset-0 w-full h-full object-cover" />
      <div className="absolute inset-0"
        style={{ background: 'linear-gradient(to top, rgba(26,26,26,0.93) 0%, rgba(26,26,26,0.55) 50%, rgba(26,26,26,0.15) 100%)' }} />
      <div className="relative z-10 px-8 md:px-16 max-w-3xl">
        <p className="hero-item text-xs font-semibold tracking-widest uppercase mb-4" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>
          Execution Intelligence
        </p>
        <h1 className="hero-item font-extrabold leading-none mb-1" style={{ fontFamily: 'var(--font-head)', color: CREAM, fontSize: 'clamp(2.8rem,7vw,5.5rem)' }}>
          Execution is the
        </h1>
        <h1 className="hero-item font-extrabold leading-none mb-3" style={{ fontFamily: 'var(--font-head)', color: CLAY, fontSize: 'clamp(2.8rem,7vw,5.5rem)' }}>
          Signal.
        </h1>
        <p className="hero-item italic mb-8" style={{ fontFamily: 'var(--font-drama)', color: 'rgba(242,240,233,0.72)', fontSize: 'clamp(1.4rem,3vw,2rem)' }}>
          Everything else is noise.
        </p>
        <p className="hero-item text-base md:text-lg mb-8 max-w-xl" style={{ color: 'rgba(242,240,233,0.68)', lineHeight: 1.75 }}>
          Lucider AI maps your Slack into a live execution graph — surfacing what's On Track, At Risk, or Failing before it's too late.
        </p>
        <a href="#" className="hero-item btn-magnetic inline-flex items-center gap-2 px-7 py-4 rounded-full text-base font-semibold"
          style={{ background: CLAY, color: CREAM }}>
          <span className="btn-bg rounded-full" style={{ background: MOSS }} />
          <span className="relative z-10">Request a demo</span>
        </a>
      </div>
    </section>
  )
}

/* ── SHUFFLER CARD — Execution Graph ── */
function ShufflerCard() {
  const items = [
    { label: 'Decisions captured',  icon: '◆', sub: 'Every call logged' },
    { label: 'Dependencies mapped', icon: '⬡', sub: 'Across all channels' },
    { label: 'Commitments tracked', icon: '●', sub: 'Owner + deadline' },
  ]
  const [stack, setStack] = useState(items)
  useEffect(() => {
    const id = setInterval(() => setStack(p => { const n=[...p]; n.unshift(n.pop()); return n }), 3000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="rounded-[2rem] p-6 h-full flex flex-col gap-3"
      style={{ background: CREAM, border: '1px solid rgba(46,64,54,0.12)', boxShadow: '0 4px 32px rgba(46,64,54,0.08)' }}>
      <div className="text-xs font-semibold tracking-widest uppercase" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>AUTO-CAPTURE</div>
      <h3 className="text-xl font-bold" style={{ color: MOSS }}>Execution Graph</h3>
      <p className="text-sm" style={{ color: 'rgba(26,26,26,0.58)' }}>Every decision, dependency, and commitment — structured automatically.</p>
      <div className="relative mt-auto" style={{ height: '120px' }}>
        {stack.map((item, i) => (
          <div key={item.label} className="absolute left-0 right-0 flex items-center gap-3 px-4 py-3 rounded-2xl"
            style={{
              background: i === 0 ? MOSS : 'rgba(46,64,54,0.07)',
              color: i === 0 ? CREAM : CHARCOAL,
              top: `${i * 22}px`, zIndex: stack.length - i,
              transition: 'all 0.6s cubic-bezier(0.34,1.56,0.64,1)',
              opacity: i > 1 ? 0.35 : 1,
            }}>
            <span style={{ color: i === 0 ? CLAY : MOSS }}>{item.icon}</span>
            <div>
              <div className="text-sm font-semibold">{item.label}</div>
              <div className="text-xs opacity-60">{item.sub}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── TYPEWRITER CARD — Program Status ── */
function TypewriterCard() {
  const lines = [
    { text: 'Project Alpha        → On Track',      cls: 'status-on-track' },
    { text: 'API Migration        → At Risk',        cls: 'status-at-risk'  },
    { text: 'Q1 Data Center       → Failing',        cls: 'status-failing'  },
    { text: 'Hiring Pipeline      → On Track',      cls: 'status-on-track' },
    { text: 'Infrastructure Audit → At Risk',        cls: 'status-at-risk'  },
  ]
  const [visible, setVisible] = useState([])
  useEffect(() => {
    let i = 0
    const id = setInterval(() => { setVisible(p => [...p.slice(-4), lines[i % lines.length]]); i++ }, 1400)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="rounded-[2rem] p-6 h-full flex flex-col gap-3"
      style={{ background: CREAM, border: '1px solid rgba(46,64,54,0.12)', boxShadow: '0 4px 32px rgba(46,64,54,0.08)' }}>
      <div className="text-xs font-semibold tracking-widest uppercase" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>PROGRAM STATUS</div>
      <h3 className="text-xl font-bold" style={{ color: MOSS }}>Live Execution Feed</h3>
      <p className="text-sm" style={{ color: 'rgba(26,26,26,0.58)' }}>On Track. At Risk. Failing. Updated in real time across every channel.</p>
      <div className="rounded-2xl p-4 mt-auto min-h-[130px] flex flex-col gap-2"
        style={{ background: CHARCOAL, fontFamily: 'var(--font-data)', fontSize: '0.7rem' }}>
        <div className="flex items-center gap-2 mb-1">
          <span className="w-2 h-2 rounded-full animate-pulse-dot" style={{ background: CLAY }} />
          <span style={{ color: CLAY }} className="text-xs tracking-widest">LIVE FEED</span>
        </div>
        {visible.map((line, i) => (
          <div key={i} className={`px-2 py-1 rounded-lg ${line.cls}`}>{line.text}</div>
        ))}
        <span className="animate-blink" style={{ color: CLAY }}>▌</span>
      </div>
    </div>
  )
}

/* ── SCHEDULER CARD — Drift Detection ── */
function SchedulerCard() {
  const days = ['S','M','T','W','T','F','S']
  const [active, setActive] = useState(null)
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    let cancel = false
    const run = async () => {
      await new Promise(r => setTimeout(r, 600))
      for (let i = 1; i <= 5; i++) {
        if (cancel) return
        setActive(i); await new Promise(r => setTimeout(r, 500))
      }
      if (cancel) return
      setSaved(true); await new Promise(r => setTimeout(r, 1200))
      setActive(null); setSaved(false)
      if (!cancel) setTimeout(run, 800)
    }
    const t = setTimeout(run, 800)
    return () => { cancel = true; clearTimeout(t) }
  }, [])
  return (
    <div className="rounded-[2rem] p-6 h-full flex flex-col gap-3"
      style={{ background: CREAM, border: '1px solid rgba(46,64,54,0.12)', boxShadow: '0 4px 32px rgba(46,64,54,0.08)' }}>
      <div className="text-xs font-semibold tracking-widest uppercase" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>DRIFT DETECTION</div>
      <h3 className="text-xl font-bold" style={{ color: MOSS }}>Silent Commitments Surfaced</h3>
      <p className="text-sm" style={{ color: 'rgba(26,26,26,0.58)' }}>Assignments gone quiet are flagged automatically — zero manual follow-up.</p>
      <div className="rounded-2xl p-4 mt-auto" style={{ background: CHARCOAL }}>
        <div className="grid grid-cols-7 gap-1 mb-3">
          {days.map((d, i) => (
            <div key={i} className="flex items-center justify-center h-8 rounded-lg text-xs font-semibold transition-all duration-300"
              style={{
                fontFamily: 'var(--font-data)',
                background: active === i ? CLAY : 'rgba(242,240,233,0.08)',
                color: active === i ? CREAM : 'rgba(242,240,233,0.45)',
                transform: active === i ? 'scale(0.95)' : 'scale(1)',
              }}>{d}</div>
          ))}
        </div>
        <div className="flex justify-between items-center">
          <span className="text-xs" style={{ color: 'rgba(242,240,233,0.4)', fontFamily: 'var(--font-data)' }}>Overdue · Assigned · Silent</span>
          <button className="text-xs px-3 py-1 rounded-full font-semibold transition-all duration-300"
            style={{ background: saved ? CLAY : 'rgba(242,240,233,0.1)', color: CREAM }}>
            {saved ? 'Flagged ✓' : 'Review'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── FEATURES ── */
function Features() {
  const ref = useRef(null)
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.feature-card', {
        scrollTrigger: { trigger: '.feature-card', start: 'top 82%' },
        y: 50, opacity: 0, duration: 0.9, stagger: 0.15, ease: 'power3.out'
      })
    }, ref)
    return () => ctx.revert()
  }, [])
  return (
    <section ref={ref} className="py-24 px-8 md:px-16" style={{ background: CREAM }}>
      <div className="max-w-6xl mx-auto">
        <p className="text-xs font-semibold tracking-widest uppercase mb-3 text-center" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>
          Three ways we eliminate failure
        </p>
        <h2 className="text-3xl md:text-5xl font-extrabold text-center mb-16" style={{ color: MOSS }}>
          Built for the way orgs actually work
        </h2>
        <div className="grid md:grid-cols-3 gap-6 items-stretch">
          <div className="feature-card"><ShufflerCard /></div>
          <div className="feature-card"><TypewriterCard /></div>
          <div className="feature-card"><SchedulerCard /></div>
        </div>
      </div>
    </section>
  )
}

/* ── PHILOSOPHY ── */
function Philosophy() {
  const ref = useRef(null)
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.phil-word', {
        scrollTrigger: { trigger: ref.current, start: 'top 70%' },
        y: 30, opacity: 0, stagger: 0.05, duration: 0.7, ease: 'power3.out'
      })
    }, ref)
    return () => ctx.revert()
  }, [])
  return (
    <section ref={ref} className="relative py-32 px-8 md:px-16 overflow-hidden" style={{ background: MOSS }}>
      <img src="https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1920&q=80&auto=format&fit=crop"
        alt="Forest" className="absolute inset-0 w-full h-full object-cover animate-op-pulse"
        style={{ opacity: 0.07, mixBlendMode: 'luminosity' }} />
      <div className="relative z-10 max-w-4xl mx-auto">
        <p className="text-base mb-8" style={{ color: 'rgba(242,240,233,0.45)' }}>Most visibility tools track tasks.</p>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {['We', 'track', 'execution.'].map((w, i) => (
            <span key={i} className="phil-word inline-block"
              style={{
                fontFamily: 'var(--font-drama)', fontStyle: 'italic', fontWeight: 700,
                fontSize: 'clamp(3rem,8vw,6.5rem)', lineHeight: 1.1,
                color: w === 'execution.' ? CLAY : CREAM,
              }}>{w}</span>
          ))}
        </div>
        <p className="mt-10 text-lg max-w-xl" style={{ color: 'rgba(242,240,233,0.60)', lineHeight: 1.8 }}>
          Decisions. Commitments. Dependencies. Drift. The signals that tell you if the org is moving — or stalling.
        </p>
      </div>
    </section>
  )
}

/* ── PROTOCOL ── */
function ProtocolCard({ num, title, desc, svg }) {
  return (
    <div className="w-full min-h-screen flex items-center justify-center px-8 md:px-16"
      style={{ background: num % 2 === 0 ? '#181F1B' : CHARCOAL }}>
      <div className="max-w-3xl w-full flex flex-col md:flex-row items-center gap-12">
        <div className="w-40 h-40 flex-shrink-0 flex items-center justify-center">{svg}</div>
        <div>
          <p className="text-xs mb-3 tracking-widest" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>0{num}</p>
          <h3 style={{ fontFamily: 'var(--font-head)', color: CREAM, fontSize: 'clamp(2.5rem,5vw,4rem)', fontWeight: 800, lineHeight: 1.1 }}>{title}</h3>
          <p className="mt-4 text-lg" style={{ color: 'rgba(242,240,233,0.58)', lineHeight: 1.8 }}>{desc}</p>
        </div>
      </div>
    </div>
  )
}

function Protocol() {
  const ref = useRef(null)
  useEffect(() => {
    const ctx = gsap.context(() => {
      const cards = gsap.utils.toArray('.protocol-card')
      cards.forEach((card, i) => {
        if (i === cards.length - 1) return
        ScrollTrigger.create({ trigger: card, start: 'top top', pin: true, pinSpacing: false,
          endTrigger: cards[cards.length - 1], end: 'bottom bottom' })
        gsap.to(card, { scale: 0.92, filter: 'blur(6px)', opacity: 0.45,
          scrollTrigger: { trigger: cards[i + 1], start: 'top 80%', end: 'top top', scrub: true } })
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  const concentric = (
    <svg viewBox="0 0 160 160" fill="none" className="w-full h-full">
      {[60,44,28,12].map((r,i) => (
        <circle key={i} cx="80" cy="80" r={r} stroke={CLAY} strokeWidth="1.5"
          strokeDasharray="4 7" opacity={0.15 + i*0.2} className="animate-slow-spin"
          style={{ animationDuration: `${10+i*4}s`, transformOrigin:'80px 80px' }} />
      ))}
      <circle cx="80" cy="80" r="5" fill={CLAY} />
    </svg>
  )
  const scanGrid = (
    <svg viewBox="0 0 160 160" fill="none" className="w-full h-full" style={{ overflow:'hidden' }}>
      {Array.from({length:5}).map((_,r) => Array.from({length:5}).map((__,c) => (
        <circle key={`${r}-${c}`} cx={20+c*30} cy={20+r*30} r="3" fill={CREAM} opacity="0.12" />
      )))}
      <rect x="10" y="0" width="4" height="160" fill={CLAY} opacity="0.8"
        style={{ animation:'scan 2.5s linear infinite' }} />
    </svg>
  )
  const ekg = (
    <svg viewBox="0 0 160 80" fill="none" className="w-full">
      <path d="M0 40 L30 40 L45 10 L55 70 L65 20 L75 40 L160 40"
        stroke={CLAY} strokeWidth="2.5" fill="none"
        strokeDasharray="300" strokeDashoffset="300"
        style={{ animation:'ekg 2s ease-in-out infinite' }} />
    </svg>
  )

  return (
    <div ref={ref}>
      <div className="protocol-card"><ProtocolCard num={1} title="Connect" svg={concentric}
        desc="Install the Lucider Slack bot in under 60 seconds. Add it to the channels that matter. No configuration required." /></div>
      <div className="protocol-card"><ProtocolCard num={2} title="Analyze" svg={scanGrid}
        desc="Every conversation analyzed in real time. Decisions, commitments, risks, and dependencies extracted automatically." /></div>
      <div className="protocol-card"><ProtocolCard num={3} title="Command" svg={ekg}
        desc="Leadership gets a live execution graph: On Track, At Risk, or Failing — across the entire org." /></div>
    </div>
  )
}

/* ── CTA ── */
function CTA() {
  return (
    <section className="py-32 px-8 text-center" style={{ background: CREAM }}>
      <p className="text-xs font-semibold tracking-widest uppercase mb-4" style={{ color: CLAY, fontFamily: 'var(--font-data)' }}>
        Ready to see it?
      </p>
      <h2 className="font-extrabold mb-6 mx-auto"
        style={{ fontFamily: 'var(--font-head)', color: MOSS, fontSize: 'clamp(2rem,5vw,3.5rem)', maxWidth:'680px', lineHeight:1.2 }}>
        Know what's failing before your team tells you.
      </h2>
      <p className="text-lg mb-10 mx-auto" style={{ color:'rgba(26,26,26,0.52)', maxWidth:'460px', lineHeight:1.8 }}>
        4-week proof of concept. Single-tenant Slack integration. Live execution graph on day one.
      </p>
      <a href="#" className="btn-magnetic inline-flex items-center gap-2 px-8 py-4 rounded-full text-base font-semibold"
        style={{ background: CLAY, color: CREAM }}>
        <span className="btn-bg rounded-full" style={{ background: MOSS }} />
        <span className="relative z-10">Request a demo</span>
      </a>
    </section>
  )
}

/* ── FOOTER ── */
function Footer() {
  return (
    <footer className="px-8 md:px-16 pt-16 pb-10 rounded-t-[4rem]" style={{ background: CHARCOAL }}>
      <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-10 mb-12">
        <div>
          <p className="text-xl font-bold mb-2" style={{ color: CREAM }}>Lucider AI</p>
          <p className="text-sm" style={{ color:'rgba(242,240,233,0.42)', lineHeight:1.7 }}>
            Execution intelligence that surfaces program status before failure.
          </p>
          <div className="flex items-center gap-2 mt-4">
            <span className="w-2 h-2 rounded-full animate-pulse-dot" style={{ background:'#4ade80' }} />
            <span className="text-xs" style={{ color:'rgba(242,240,233,0.35)', fontFamily:'var(--font-data)' }}>SYSTEM OPERATIONAL</span>
          </div>
        </div>
        <div>
          <p className="text-xs font-semibold tracking-widest uppercase mb-4" style={{ color:'rgba(242,240,233,0.28)', fontFamily:'var(--font-data)' }}>PRODUCT</p>
          {['Features','How it works','Pricing','Changelog'].map(l => (
            <a key={l} href="#" className="block text-sm mb-2 hover:-translate-y-px transition-transform"
              style={{ color:'rgba(242,240,233,0.52)' }}>{l}</a>
          ))}
        </div>
        <div>
          <p className="text-xs font-semibold tracking-widest uppercase mb-4" style={{ color:'rgba(242,240,233,0.28)', fontFamily:'var(--font-data)' }}>COMPANY</p>
          {['Privacy','Terms','Security','Contact'].map(l => (
            <a key={l} href="#" className="block text-sm mb-2 hover:-translate-y-px transition-transform"
              style={{ color:'rgba(242,240,233,0.52)' }}>{l}</a>
          ))}
        </div>
      </div>
      <div className="border-t pt-6 flex flex-col md:flex-row justify-between items-center gap-3"
        style={{ borderColor:'rgba(242,240,233,0.07)' }}>
        <p className="text-xs" style={{ color:'rgba(242,240,233,0.22)', fontFamily:'var(--font-data)' }}>© 2026 Lucider AI. All rights reserved.</p>
        <p className="text-xs" style={{ color:'rgba(242,240,233,0.22)', fontFamily:'var(--font-data)' }}>Built with Claude</p>
      </div>
    </footer>
  )
}

export default function App() {
  return (
    <>
      <Navbar />
      <Hero />
      <Features />
      <Philosophy />
      <Protocol />
      <CTA />
      <Footer />
    </>
  )
}
