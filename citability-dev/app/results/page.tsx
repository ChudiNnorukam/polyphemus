"use client"

import { useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"
import { ArrowRight, ExternalLink, Shield, AlertTriangle, Target, Share2, Check, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const goalLabels: Record<string, string> = {
  benchmark: "understanding your competitive position",
  first_mention: "getting mentioned by AI for the first time",
  citations: "getting actual URL citations from AI",
  strategy: "building a complete AI visibility strategy",
}

function ShareButton() {
  const [copied, setCopied] = useState(false)
  const handleShare = () => {
    navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={handleShare}
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-teal transition-colors"
    >
      {copied ? <Check className="w-4 h-4" /> : <Share2 className="w-4 h-4" />}
      {copied ? "Link copied!" : "Share your report"}
    </button>
  )
}

function ResultsContent() {
  const params = useSearchParams()
  const name = params.get("name") || "there"
  const url = params.get("url") || ""
  const goal = params.get("goal") || ""
  const solution = params.get("solution") || ""
  const scanRaw = params.get("scan") || ""

  let scan: { score: number; total: number; checks: Array<{ name: string; slug: string; pass: boolean; detail: string }>; url: string } | null = null
  try {
    if (scanRaw) scan = JSON.parse(scanRaw)
  } catch { /* no scan data */ }

  const score = scan?.score ?? 0
  const total = scan?.total ?? 10
  const scorePercent = Math.round((score / total) * 100)

  const tier = score >= 8 ? "high" : score >= 5 ? "medium" : "low"

  const tierLabels: Record<string, string> = {
    high: "FOUNDATION STRONG",
    medium: "FOUNDATION READY",
    low: "FOUNDATION GAPS",
  }

  const tierColors: Record<string, string> = {
    high: "text-teal",
    medium: "text-yellow-400",
    low: "text-red-400",
  }

  const headlines: Record<string, string> = {
    high: `Great news, ${name}. Your site has strong citation-readiness foundations.`,
    medium: `${name}, your site has a solid base but several citation-readiness gaps remain.`,
    low: `${name}, your site needs foundational work before AI systems are likely to retrieve and cite it consistently.`,
  }

  const passes = scan?.checks.filter((c) => c.pass) || []
  const fails = scan?.checks.filter((c) => !c.pass) || []

  // CTA based on solution preference
  const ctaConfig: Record<string, { label: string; description: string; href: string }> = {
    done_for_you: {
      label: "Book a Strategy Call ($497)",
      description: "Complete audit with 30-min call, implementation roadmap, and competitor comparison.",
      href: "mailto:hello@citability.dev?subject=Full%20Audit%20Request&body=I%20just%20took%20the%20free%20assessment%20and%20scored%20" + score + "%2F" + total + ".%20I%27d%20like%20the%20full%20audit%20with%20strategy%20call.",
    },
    consult: {
      label: "Book a Strategy Call ($497)",
      description: "30 minutes with an AI visibility expert to map your next steps.",
      href: "mailto:hello@citability.dev?subject=Strategy%20Call%20Request&body=I%20just%20took%20the%20free%20assessment%20and%20scored%20" + score + "%2F" + total + ".%20I%27d%20like%20to%20book%20a%20strategy%20call.",
    },
    report: {
      label: "Get Your Quick Report ($147)",
      description: "Live AI visibility + citability test with 20 real queries across ChatGPT, Perplexity, and Claude.",
      href: "mailto:hello@citability.dev?subject=Quick%20Report%20Request&body=I%20just%20took%20the%20free%20assessment%20and%20scored%20" + score + "%2F" + total + ".%20I%27d%20like%20to%20order%20a%20Quick%20Report.",
    },
    diy: {
      label: "Get the Implementation Checklist",
      description: "A prioritized list of exactly what to fix, starting with the highest-impact items.",
      href: "https://github.com/ChudiNnorukam/ai-visibility-readiness",
    },
  }

  const cta = ctaConfig[solution] || ctaConfig.diy

  return (
    <main className="min-h-screen bg-background text-foreground px-4 py-16">
      <div className="max-w-2xl mx-auto flex flex-col gap-10">

        {/* Header */}
        <section className="text-center flex flex-col items-center gap-6">
          <div className="inline-flex items-center gap-2 border border-teal/30 bg-teal/5 rounded-full px-4 py-1.5">
            <span className="font-mono text-xs text-teal tracking-widest uppercase">
              Your Free Report
            </span>
          </div>

          {/* Score ring */}
          <div className="relative w-40 h-40 flex items-center justify-center">
            <svg className="absolute inset-0 w-full h-full -rotate-90" viewBox="0 0 120 120">
              <circle cx="60" cy="60" r="52" fill="none" stroke="var(--border)" strokeWidth="8" />
              <circle
                cx="60" cy="60" r="52" fill="none"
                stroke={tier === "high" ? "var(--teal)" : tier === "medium" ? "#facc15" : "#f87171"}
                strokeWidth="8"
                strokeDasharray={`${scorePercent * 3.27} 327`}
                strokeLinecap="round"
                className="transition-all duration-1000"
              />
            </svg>
            <div className="text-center">
              <span className={cn("font-mono text-3xl font-bold", tierColors[tier])}>
                {score}
              </span>
              <span className="font-mono text-lg text-muted-foreground">/{total}</span>
            </div>
          </div>

          <span className={cn(
            "font-mono text-sm px-3 py-1 rounded border",
            tier === "high" && "text-teal border-teal/30 bg-teal/10",
            tier === "medium" && "text-yellow-400 border-yellow-400/30 bg-yellow-400/10",
            tier === "low" && "text-red-400 border-red-400/30 bg-red-400/10",
          )}>
            {tierLabels[tier]}
          </span>

          {url && (
            <p className="font-mono text-xs text-muted-foreground">
              Scanned: {url}
            </p>
          )}

          <h1 className="text-2xl sm:text-3xl font-semibold text-foreground text-balance leading-snug">
            {headlines[tier]}
          </h1>
        </section>

        {/* Scan Results - the real data */}
        {scan && (
          <section>
            <h2 className="font-mono text-xs text-teal tracking-widest uppercase mb-4">
              Infrastructure Scan Results
            </h2>
            <div className="border border-border rounded-lg overflow-hidden">
              <div className="flex items-center gap-2 px-5 py-3 border-b border-border bg-navy-elevated">
                <Shield className="w-4 h-4 text-teal" />
                <span className="font-mono text-sm text-foreground">
                  {score}/{total} checks passed
                </span>
              </div>
              {scan.checks.map((check: any, i: number) => (
                <div
                  key={check.slug}
                  className={cn(
                    "flex flex-col px-5 py-3.5 border-b border-border/60",
                    i % 2 === 0 ? "bg-background/60" : "bg-navy-surface/60"
                  )}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm text-foreground font-medium">{check.name}</p>
                        {check.evidence && (
                          <span className={cn(
                            "font-mono text-[10px] px-1.5 py-0.5 rounded border",
                            check.evidence === "verified"
                              ? "text-teal/70 border-teal/20 bg-teal/5"
                              : "text-yellow-400/70 border-yellow-400/20 bg-yellow-400/5"
                          )}>
                            {check.evidence === "verified" ? "VERIFIED" : "EMERGING"}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">{check.detail}</p>
                      {check.why && (
                        <p className="text-[11px] text-muted-foreground/60 mt-1 italic">{check.why}</p>
                      )}
                    </div>
                    <span className={cn(
                      "font-mono text-xs px-2 py-0.5 rounded border ml-3 flex-shrink-0",
                      check.pass
                        ? "text-teal border-teal/30 bg-teal/10"
                        : "text-red-400 border-red-400/30 bg-red-400/10"
                    )}>
                      {check.pass ? "PASS" : "FAIL"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Key Insights */}
        <section className="flex flex-col gap-4">
          <h2 className="font-mono text-xs text-teal tracking-widest uppercase">
            What This Means
          </h2>

          {passes.length > 0 && (
            <div className="border border-border bg-navy-surface rounded-lg p-5 flex gap-4">
              <Check className="w-5 h-5 text-teal flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground mb-1">What&apos;s working</p>
                <p className="text-sm text-muted-foreground">
                  You have {passes.map((p) => p.name).join(", ")} in place.
                  {passes.length >= 7 ? " That puts you ahead of 85% of sites we scan." : " A solid foundation to build on."}
                </p>
              </div>
            </div>
          )}

          {fails.length > 0 && (
            <div className="border border-border bg-navy-surface rounded-lg p-5 flex gap-4">
              <AlertTriangle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground mb-1">What&apos;s missing</p>
                <p className="text-sm text-muted-foreground">
                  You&apos;re missing {fails.map((f) => f.name).join(", ")}.
                  {" "}These are the discovery and content-quality signals most likely to improve retrieval and citation readiness.
                </p>
              </div>
            </div>
          )}

          {goal && goalLabels[goal] && (
            <div className="border border-border bg-navy-surface rounded-lg p-5 flex gap-4">
              <Target className="w-5 h-5 text-teal flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground mb-1">Your goal</p>
                <p className="text-sm text-muted-foreground">
                  You said your priority is {goalLabels[goal]}.
                  {tier === "high"
                    ? " Your infrastructure supports this. The next step is live AI testing to measure actual visibility and citations."
                    : tier === "medium"
                      ? ` Fixing ${fails.length > 0 ? fails[0].name : "your gaps"} first will strengthen both discovery and citation-worthiness.`
                      : " The foundation comes first. AI systems are unlikely to cite pages they cannot reliably retrieve or interpret."}
                </p>
              </div>
            </div>
          )}
        </section>

        {/* What this report doesn't cover */}
        <section className="border border-border bg-navy-surface/50 rounded-lg p-5">
          <button
            className="w-full flex items-center justify-between text-left"
            onClick={(e) => {
              const content = (e.currentTarget.nextElementSibling as HTMLElement)
              content.classList.toggle("hidden")
            }}
          >
            <span className="text-sm font-medium text-foreground">What this free report doesn&apos;t cover</span>
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          </button>
          <div className="hidden mt-3 text-sm text-muted-foreground space-y-2">
            <p>This scan checks your <strong>HTML-visible infrastructure and content-readiness signals</strong>. It does NOT test:</p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong>AI Visibility</strong>: Does ChatGPT/Perplexity actually know your brand? (requires live API queries)</li>
              <li><strong>AI Recommendability</strong>: Does AI suggest you when asked about your topic? (requires 20+ test queries)</li>
              <li><strong>AI Citability</strong>: Does AI link to your URL as a source? (requires multi-platform testing)</li>
            </ul>
            <p>These require the <strong>Quick Report ($147)</strong> or <strong>Full Audit ($497)</strong>.</p>
          </div>
        </section>

        {/* CTA */}
        <section className="border border-teal/30 bg-teal/5 rounded-lg p-6 flex flex-col gap-4 items-center text-center">
          <h2 className="text-xl font-semibold text-foreground">Your Recommended Next Step</h2>
          <p className="text-sm text-muted-foreground max-w-md">{cta.description}</p>
          <a href={cta.href}>
            <Button className="h-12 px-8 bg-teal text-primary-foreground font-semibold hover:bg-teal-dim flex items-center gap-2">
              {cta.label}
              <ArrowRight className="w-4 h-4" />
            </Button>
          </a>
        </section>

        {/* Share */}
        <div className="text-center">
          <ShareButton />
        </div>

        {/* Footer */}
        <footer className="text-center flex flex-col gap-3 pt-6 border-t border-border">
          <a
            href="https://citability.dev"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-teal transition-colors mx-auto"
          >
            citability.dev
            <ExternalLink className="w-3 h-3" />
          </a>
          <p className="font-mono text-xs text-muted-foreground/50">
            Powered by the AI Visibility Readiness (AVR) Framework v1.0
          </p>
        </footer>
      </div>
    </main>
  )
}

export default function ResultsPage() {
  return (
    <Suspense fallback={
      <main className="min-h-screen bg-background text-foreground flex items-center justify-center">
        <span className="font-mono text-sm text-muted-foreground">Loading results...</span>
      </main>
    }>
      <ResultsContent />
    </Suspense>
  )
}
