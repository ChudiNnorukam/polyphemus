"use client"

import { useState } from "react"
import { ArrowRight, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function Hero() {
  const [domain, setDomain] = useState("")

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const url = domain.trim() ? `?url=${encodeURIComponent(domain.trim())}` : ""
    window.location.href = `/assess${url}`
  }

  const scrollToComparison = () => {
    document.getElementById("comparison")?.scrollIntoView({ behavior: "smooth" })
  }

  return (
    <section className="relative flex flex-col items-center justify-center px-4 pt-32 pb-20 overflow-hidden">
      {/* Grid background */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage:
            "linear-gradient(var(--grid-line) 1px, transparent 1px), linear-gradient(90deg, var(--grid-line) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
          opacity: 0.4,
        }}
      />
      {/* Radial glow behind headline */}
      <div
        className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[400px] pointer-events-none"
        style={{
          background: "radial-gradient(ellipse at center, oklch(0.78 0.18 192 / 0.10) 0%, transparent 70%)",
        }}
      />

      <div className="relative z-10 max-w-4xl w-full text-center flex flex-col items-center gap-6">
        {/* Label */}
        <div className="inline-flex items-center gap-2 border border-teal/30 bg-teal/5 rounded-full px-4 py-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-teal animate-pulse" />
          <span className="font-mono text-xs text-teal tracking-widest uppercase">
            AI Visibility Auditing
          </span>
        </div>

        {/* Headline - Priestley Frustration Hook */}
        <h1 className="text-3xl sm:text-4xl md:text-5xl lg:text-6xl font-semibold text-foreground leading-tight text-balance tracking-tighter">
          Frustrated that AI never mentions your brand,{" "}
          <span className="text-teal">even though you invest in SEO?</span>
        </h1>

        {/* Subhead - directs to assessment */}
        <p className="max-w-2xl text-base sm:text-lg leading-relaxed text-muted-foreground text-pretty">
          Answer 15 questions to find out why ChatGPT, Perplexity, and Claude can&apos;t find you,
          and get a personalized plan to fix it.
        </p>

        {/* Value Proposition - 3 key areas */}
        <div className="w-full max-w-lg text-left flex flex-col gap-2">
          <p className="text-sm text-muted-foreground text-center">We&apos;ll measure and improve:</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {[
              { num: "01", label: "AI Infrastructure" },
              { num: "02", label: "Brand Visibility" },
              { num: "03", label: "Content Citability" },
            ].map(({ num, label }) => (
              <div key={num} className="flex items-center gap-2 border border-border bg-navy-surface rounded-md px-3 py-2">
                <span className="font-mono text-xs text-teal">{num}</span>
                <span className="text-sm text-foreground">{label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Domain input + CTA */}
        <form
          onSubmit={handleSubmit}
          className="mt-2 w-full max-w-xl flex flex-col sm:flex-row gap-3"
        >
          <div className="flex-1 relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 font-mono text-sm text-muted-foreground select-none">
              https://
            </span>
            <Input
              type="text"
              placeholder="yourdomain.com"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              className="pl-[4.5rem] h-12 bg-navy-surface border-border text-foreground placeholder:text-muted-foreground font-mono text-sm focus-visible:ring-teal focus-visible:border-teal/50 rounded-md"
            />
          </div>
          <Button
            type="submit"
            className="h-12 px-6 bg-teal text-primary-foreground font-semibold hover:bg-teal-dim transition-colors whitespace-nowrap flex items-center gap-2 rounded-md"
          >
            Start Free Assessment
            <ArrowRight className="w-4 h-4" />
          </Button>
        </form>

        {/* Friction reducer - Priestley CTA formula */}
        <p className="text-xs text-muted-foreground font-mono">
          Takes 3 minutes. Completely free. Immediate recommendations.
        </p>

        {/* Secondary CTA */}
        <button
          onClick={scrollToComparison}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-teal transition-colors group"
        >
          See How Top Sites Score
          <ChevronDown className="w-4 h-4 group-hover:translate-y-0.5 transition-transform" />
        </button>

        {/* Signature: Audit Preview */}
        <div className="mt-8 w-full max-w-md border border-border bg-navy-surface rounded-lg overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-navy-elevated">
            <span className="w-2 h-2 rounded-full bg-teal animate-pulse" />
            <span className="font-mono text-xs text-muted-foreground">sample audit output</span>
          </div>
          <div className="px-4 py-3 flex flex-col gap-1.5 font-mono text-xs">
            {[
              { check: "robots.txt", pass: true },
              { check: "sitemap.xml", pass: true },
              { check: "llms.txt", pass: false },
              { check: "structured data", pass: true },
              { check: "ai.txt", pass: false },
            ].map(({ check, pass }) => (
              <div key={check} className="flex items-center gap-2">
                <span className={pass ? "text-teal" : "text-red-400"}>
                  {pass ? "PASS" : "FAIL"}
                </span>
                <span className="text-muted-foreground">{check}</span>
              </div>
            ))}
            <div className="mt-1 pt-2 border-t border-border flex items-baseline gap-2">
              <span className="text-teal font-semibold">3/5</span>
              <span className="text-muted-foreground">infrastructure checks passed</span>
            </div>
          </div>
        </div>

        {/* Stat pills */}
        <div className="mt-6 flex flex-wrap justify-center gap-4">
          {[
            { label: "AI Platforms Tested", value: "3" },
            { label: "Queries Per Audit", value: "20" },
            { label: "Infrastructure Signals", value: "12" },
          ].map(({ label, value }) => (
            <div
              key={label}
              className="flex items-baseline gap-2.5 border border-border bg-navy-surface rounded-md px-5 py-3"
            >
              <span className="font-mono text-2xl font-bold text-teal">{value}</span>
              <span className="text-xs text-muted-foreground">{label}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
