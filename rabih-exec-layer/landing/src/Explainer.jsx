import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

const C = {
  obsidian:  '#0D0D12',
  champagne: '#C9A84C',
  ivory:     '#FAF8F5',
  slate:     '#2A2A35',
}

// ── Navbar (same pill, ivory background) ──────────────────────────────────
function Navbar() {
  return (
    <nav className="fixed top-4 left-1/2 z-50 flex items-center gap-6 px-6 py-3 rounded-full"
         style={{
           transform: 'translateX(-50%)',
           background: 'rgba(250,248,245,0.85)',
           backdropFilter: 'blur(20px)',
           border: '1px solid rgba(13,13,18,0.08)',
           boxShadow: '0 4px 24px rgba(0,0,0,0.06)',
         }}>
      <Link to="/" className="font-sans font-bold text-sm tracking-tight no-underline"
            style={{ color: C.obsidian }}>
        Lucider AI
      </Link>
      <div className="hidden md:flex items-center gap-1">
        {['Features', 'How it works', 'Pricing'].map(l => (
          <Link key={l} to={`/#${l.toLowerCase().replace(/ /g,'-')}`}
                className="nav-link px-3 py-1 text-sm font-medium no-underline"
                style={{ color: C.slate }}>
            {l}
          </Link>
        ))}
      </div>
      <Link to="/" className="btn-magnetic px-4 py-2 rounded-full text-sm font-semibold no-underline"
            style={{ background: C.champagne, color: C.obsidian }}>
        <span className="relative z-10">Book a demo</span>
        <span className="btn-bg rounded-full" style={{ background: '#b8933a' }} />
      </Link>
    </nav>
  )
}

// ── Slack convo mock ───────────────────────────────────────────────────────
function SlackMock({ messages }) {
  return (
    <div className="rounded-2xl overflow-hidden border"
         style={{ background: '#1a1d21', borderColor: 'rgba(255,255,255,0.06)' }}>
      <div className="px-4 py-2 border-b flex items-center gap-2"
           style={{ borderColor: 'rgba(255,255,255,0.06)', background: '#19191c' }}>
        <span className="font-mono text-xs" style={{ color: 'rgba(255,255,255,0.35)' }}># engineering</span>
      </div>
      <div className="p-4 space-y-3">
        {messages.map((m, i) => (
          <div key={i} className="flex gap-3 items-start">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold shrink-0"
                 style={{ background: m.color, color: '#fff' }}>
              {m.name[0]}
            </div>
            <div>
              <div className="flex items-baseline gap-2 mb-1">
                <span className="text-sm font-semibold" style={{ color: '#fff' }}>{m.name}</span>
                <span className="text-xs" style={{ color: 'rgba(255,255,255,0.3)' }}>{m.time}</span>
              </div>
              <p className="text-sm leading-relaxed" style={{ color: 'rgba(255,255,255,0.7)' }}>
                {m.highlight ? (
                  <>
                    {m.text.split(m.highlight)[0]}
                    <span className="px-1 rounded" style={{ background: 'rgba(201,168,76,0.25)', color: C.champagne }}>
                      {m.highlight}
                    </span>
                    {m.text.split(m.highlight)[1]}
                  </>
                ) : m.text}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Extracted result card ──────────────────────────────────────────────────
function ExtractedCard({ type, text, meta }) {
  const colors = {
    decision: { bg: 'rgba(201,168,76,0.1)', border: 'rgba(201,168,76,0.3)', label: C.champagne },
    action:   { bg: 'rgba(59,130,246,0.08)', border: 'rgba(59,130,246,0.25)', label: '#60a5fa' },
    risk:     { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)', label: '#f87171' },
  }
  const s = colors[type]
  const icons = { decision: '✓', action: '→', risk: '⚠' }
  return (
    <div className="rounded-2xl px-4 py-3 border"
         style={{ background: s.bg, borderColor: s.border }}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-mono font-semibold uppercase tracking-wider" style={{ color: s.label }}>
          {icons[type]} {type}
        </span>
      </div>
      <p className="text-sm" style={{ color: C.obsidian }}>{text}</p>
      {meta && <p className="text-xs mt-1" style={{ color: 'rgba(13,13,18,0.45)' }}>{meta}</p>}
    </div>
  )
}

// ── Section wrapper ────────────────────────────────────────────────────────
function Section({ children, dark = false, className = '' }) {
  return (
    <section className={`py-24 px-6 md:px-16 ${className}`}
             style={{ background: dark ? C.obsidian : C.ivory }}>
      <div className="max-w-4xl mx-auto">{children}</div>
    </section>
  )
}

function Label({ children }) {
  return (
    <p className="font-mono text-xs tracking-widest uppercase mb-4" style={{ color: C.champagne }}>
      {children}
    </p>
  )
}

// ── Main explainer ─────────────────────────────────────────────────────────
export default function Explainer() {
  const heroRef = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.ex-fade', {
        y: 30, opacity: 0, duration: 0.9, stagger: 0.1, ease: 'power3.out', delay: 0.1,
      })
    }, heroRef)
    return () => ctx.revert()
  }, [])

  return (
    <>
      <Navbar />

      {/* ── Hero ── */}
      <section ref={heroRef} className="pt-36 pb-20 px-6 md:px-16"
               style={{ background: C.ivory }}>
        <div className="max-w-4xl mx-auto">
          <Label>Plain English</Label>
          <h1 className="ex-fade font-sans font-black leading-tight mb-6"
              style={{ fontSize: 'clamp(2.5rem,6vw,5rem)', color: C.obsidian }}>
            What is Lucider AI,<br />
            <span className="font-drama italic" style={{ color: C.champagne }}>actually?</span>
          </h1>
          <p className="ex-fade text-xl leading-relaxed max-w-2xl"
             style={{ color: C.slate }}>
            No jargon. No buzzwords. Just a straight explanation of what this
            tool does, who it helps, and why it matters.
          </p>
        </div>
      </section>

      {/* ── The Problem ── */}
      <Section>
        <Label>The Problem</Label>
        <h2 className="font-sans font-black text-4xl mb-6" style={{ color: C.obsidian }}>
          Your company runs on Slack.<br />Slack doesn't remember anything.
        </h2>
        <p className="text-lg leading-relaxed mb-10" style={{ color: C.slate }}>
          Every day, your team makes real decisions inside Slack messages. Someone
          commits to finishing a feature. A risk gets flagged. Leadership agrees on
          a direction. Then the conversation scrolls up and disappears — no record,
          no owner, no follow-up.
        </p>
        <SlackMock messages={[
          { name: 'Sarah', color: '#7c3aed', time: '10:02 AM',
            text: "Ok let's go with the AWS migration. Confirmed.",
            highlight: "let's go with the AWS migration. Confirmed." },
          { name: 'Mike',  color: '#0284c7', time: '10:04 AM',
            text: "Got it. I'll have the staging environment ready by Friday.",
            highlight: "I'll have the staging environment ready by Friday." },
          { name: 'Tom',   color: '#dc2626', time: '10:06 AM',
            text: "Worth noting — this could break existing session tokens in prod.",
            highlight: "could break existing session tokens in prod." },
          { name: 'Sarah', color: '#7c3aed', time: '10:07 AM',
            text: "Good point. Mike, add a migration runbook to the scope too.",
            highlight: "add a migration runbook to the scope too." },
        ]} />
        <div className="mt-6 rounded-2xl px-6 py-5 border"
             style={{ background: 'rgba(13,13,18,0.04)', borderColor: 'rgba(13,13,18,0.08)' }}>
          <p className="text-sm font-semibold mb-1" style={{ color: C.obsidian }}>
            What happens to this conversation by Monday?
          </p>
          <p className="text-sm" style={{ color: C.slate }}>
            It scrolls up. Mike may or may not remember both tasks. The risk about
            session tokens is never reviewed. Leadership has no idea a major
            architectural decision was made. Three weeks later, prod breaks.
          </p>
        </div>
      </Section>

      {/* ── The Solution ── */}
      <Section dark>
        <Label>The Solution</Label>
        <h2 className="font-sans font-black text-4xl mb-6" style={{ color: C.ivory }}>
          Lucider AI reads the thread.<br />
          <span className="font-drama italic" style={{ color: C.champagne }}>
            AI extracts the structure.
          </span>
        </h2>
        <p className="text-lg leading-relaxed mb-10" style={{ color: 'rgba(250,248,245,0.65)' }}>
          The same conversation above gets read by our AI and turned into this —
          automatically, with no workflow change for your team:
        </p>
        <div className="space-y-3">
          <ExtractedCard
            type="decision"
            text="Migrate to AWS"
            meta="Decided by: Sarah · #engineering · 10:02 AM"
          />
          <ExtractedCard
            type="action"
            text="Have staging environment ready by Friday"
            meta="Owner: Mike · Due: Friday · Confidence: 94%"
          />
          <ExtractedCard
            type="action"
            text="Write migration runbook"
            meta="Owner: Mike · Due: not specified · Confidence: 88%"
          />
          <ExtractedCard
            type="risk"
            text="Migration could break existing session tokens in production"
            meta="Raised by: Tom · Severity: high · Still open"
          />
        </div>
      </Section>

      {/* ── What is Drift ── */}
      <Section>
        <Label>What is "Drift"?</Label>
        <h2 className="font-sans font-black text-4xl mb-6" style={{ color: C.obsidian }}>
          A commitment made,<br />then
          <span className="font-drama italic" style={{ color: C.champagne }}> never mentioned again.</span>
        </h2>
        <p className="text-lg leading-relaxed mb-8" style={{ color: C.slate }}>
          Drift is what happens when Mike agrees to "have staging ready by Friday"
          — and then nobody ever checks in, nobody follows up, and Friday comes and
          goes with nothing done. Not because Mike is unreliable. Because there was
          no system tracking it.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[
            { day: 'Monday',   status: 'committed',  note: 'Mike commits to staging env by Friday', color: C.champagne },
            { day: 'Wednesday', status: 'silent',    note: '0 messages mentioning staging or Mike\'s task', color: '#f97316' },
            { day: 'Friday',   status: 'drifted',    note: 'Lucider AI flags: "staging env — 5 days silent"', color: '#ef4444' },
          ].map(d => (
            <div key={d.day} className="rounded-2xl p-5 border"
                 style={{ borderColor: 'rgba(13,13,18,0.08)', background: 'rgba(13,13,18,0.02)' }}>
              <p className="font-mono text-xs mb-2" style={{ color: d.color }}>{d.day.toUpperCase()}</p>
              <p className="font-semibold text-sm mb-1" style={{ color: C.obsidian }}>{d.status}</p>
              <p className="text-sm leading-relaxed" style={{ color: C.slate }}>{d.note}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ── Who it's for ── */}
      <Section dark>
        <Label>Who it's for</Label>
        <h2 className="font-sans font-black text-4xl mb-10" style={{ color: C.ivory }}>
          Built for the people who need to see<br />
          <span className="font-drama italic" style={{ color: C.champagne }}>what's actually happening.</span>
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {[
            {
              role: 'Founders & CEOs',
              pain: '"I have no idea what we actually decided in that Slack thread last week."',
              gain: 'See every decision logged with who made it, when, and what actions came out of it.',
            },
            {
              role: 'Engineering Leads',
              pain: '"I have to manually chase people to find out what the status is."',
              gain: 'Open actions with owners surface automatically. No status meetings needed.',
            },
            {
              role: 'Operations',
              pain: '"Risks get raised in Slack and then nobody does anything about them."',
              gain: 'Every raised risk is logged, tagged by severity, and stays visible until resolved.',
            },
            {
              role: 'Product Managers',
              pain: '"How do I know which decisions were actually made vs. just discussed?"',
              gain: 'AI confidence scores separate firm decisions from exploratory conversation.',
            },
          ].map(p => (
            <div key={p.role} className="rounded-2xl p-6 border"
                 style={{ borderColor: 'rgba(250,248,245,0.08)', background: 'rgba(250,248,245,0.03)' }}>
              <p className="font-sans font-bold text-base mb-3" style={{ color: C.champagne }}>{p.role}</p>
              <p className="text-sm italic mb-4 leading-relaxed" style={{ color: 'rgba(250,248,245,0.4)' }}>
                {p.pain}
              </p>
              <p className="text-sm leading-relaxed" style={{ color: 'rgba(250,248,245,0.7)' }}>
                {p.gain}
              </p>
            </div>
          ))}
        </div>
      </Section>

      {/* ── How it works ── */}
      <Section>
        <Label>How it works</Label>
        <h2 className="font-sans font-black text-4xl mb-12" style={{ color: C.obsidian }}>
          Three steps. Zero workflow changes.
        </h2>
        <div className="space-y-6">
          {[
            {
              num: '01',
              title: 'You install the Slack bot',
              body: 'Takes about 60 seconds. You add Lucider AI to the channels you want it to watch — usually #engineering, #product, #decisions. Your team doesn\'t need to change how they write messages.',
            },
            {
              num: '02',
              title: 'AI reads every thread silently',
              body: 'Claude (the same AI that powers Claude.ai) reads each conversation. It\'s looking for specific language patterns: firm agreements, task assignments, flagged risks. It gives each extraction a confidence score — and only keeps the ones it\'s sure about.',
            },
            {
              num: '03',
              title: 'Leadership sees the dashboard',
              body: 'A clean web dashboard shows every logged decision, open action, raised risk, and drifting commitment — updated hourly. No new tool for your team. Just visibility for leadership.',
            },
          ].map((s, i) => (
            <div key={s.num} className="flex gap-8 items-start">
              <span className="font-mono text-4xl font-bold shrink-0 leading-none"
                    style={{ color: 'rgba(13,13,18,0.1)' }}>
                {s.num}
              </span>
              <div className="pt-1">
                <h3 className="font-sans font-bold text-xl mb-3" style={{ color: C.obsidian }}>
                  {s.title}
                </h3>
                <p className="text-base leading-relaxed" style={{ color: C.slate }}>
                  {s.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* ── FAQ ── */}
      <Section dark>
        <Label>Common questions</Label>
        <h2 className="font-sans font-black text-4xl mb-10" style={{ color: C.ivory }}>
          Straight answers.
        </h2>
        <div className="space-y-8">
          {[
            {
              q: 'Does my team need to do anything differently?',
              a: 'No. They write Slack messages exactly as they always have. Lucider AI runs in the background. Nobody on your team needs to learn a new tool or change their habits.',
            },
            {
              q: 'What if the AI gets it wrong?',
              a: 'Every extraction has a confidence score. We only show items the AI is 70%+ confident about. Lower-confidence items are discarded. The goal is precision over recall — a dashboard with 10 accurate items beats one with 30 noisy ones.',
            },
            {
              q: 'Does it read private messages?',
              a: 'No. Lucider AI only accesses channels you explicitly add the bot to. It never reads DMs or private channels it hasn\'t been invited to.',
            },
            {
              q: 'How is this different from just reading Slack?',
              a: 'You can\'t manually read 200+ Slack threads per week and extract structure from them. Lucider AI does it in seconds, formats it consistently, tracks it over time, and alerts you when something goes silent. The value is the structure, not the reading.',
            },
            {
              q: 'What does "drift" mean exactly?',
              a: 'An action was committed to in Slack (e.g. "I\'ll have this done by Friday") but nobody has mentioned it again in N days. It\'s not necessarily a failure yet — but it\'s a signal that something might be slipping through the cracks.',
            },
          ].map(item => (
            <div key={item.q} className="border-b pb-8" style={{ borderColor: 'rgba(250,248,245,0.07)' }}>
              <p className="font-sans font-semibold text-lg mb-3" style={{ color: C.ivory }}>{item.q}</p>
              <p className="text-base leading-relaxed" style={{ color: 'rgba(250,248,245,0.6)' }}>{item.a}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ── CTA ── */}
      <section className="py-28 px-6 text-center" style={{ background: C.ivory }}>
        <p className="font-mono text-xs tracking-widest uppercase mb-4" style={{ color: C.champagne }}>
          Ready?
        </p>
        <h2 className="font-drama italic text-5xl md:text-6xl mb-6 leading-tight" style={{ color: C.obsidian }}>
          See it on your<br />own Slack workspace.
        </h2>
        <p className="text-lg max-w-md mx-auto mb-10" style={{ color: C.slate }}>
          15-minute demo. We'll show you exactly what Lucider AI would have caught
          in your last week of Slack activity.
        </p>
        <Link to="/"
              className="btn-magnetic inline-block px-10 py-5 rounded-full text-base font-bold no-underline"
              style={{ background: C.obsidian, color: C.ivory }}>
          <span className="relative z-10">Book a demo</span>
          <span className="btn-bg rounded-full" style={{ background: C.slate }} />
        </Link>
      </section>

      {/* ── Footer ── */}
      <footer className="px-8 py-12 rounded-t-[4rem]" style={{ background: C.obsidian }}>
        <div className="max-w-4xl mx-auto flex flex-col md:flex-row justify-between items-start gap-6">
          <div>
            <p className="font-sans font-bold text-base mb-1" style={{ color: C.ivory }}>Lucider AI</p>
            <p className="text-sm" style={{ color: 'rgba(250,248,245,0.35)' }}>
              Execution intelligence from Slack.
            </p>
            <div className="flex items-center gap-2 mt-4">
              <span className="op-dot w-2 h-2 rounded-full" style={{ background: '#22c55e' }} />
              <span className="font-mono text-xs" style={{ color: 'rgba(250,248,245,0.35)' }}>
                SYSTEM OPERATIONAL
              </span>
            </div>
          </div>
          <div className="flex gap-8">
            <div>
              <p className="font-mono text-xs uppercase tracking-widest mb-3"
                 style={{ color: 'rgba(250,248,245,0.25)' }}>Pages</p>
              <Link to="/" className="nav-link block text-sm mb-2 no-underline"
                    style={{ color: 'rgba(250,248,245,0.5)' }}>Home</Link>
              <Link to="/explainer" className="nav-link block text-sm mb-2 no-underline"
                    style={{ color: C.champagne }}>Plain English</Link>
            </div>
          </div>
        </div>
      </footer>
    </>
  )
}
