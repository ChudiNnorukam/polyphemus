"use client"

import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { ArrowRight, ExternalLink, TrendingUp, AlertTriangle, Target } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const bestPracticeLabels: Record<string, string> = {
  q1: "robots.txt",
  q2: "sitemap.xml",
  q3: "structured data (JSON-LD)",
  q4: "/llms.txt",
  q5: "question-format headings",
  q6: "original data and case studies",
  q7: "AI brand monitoring",
  q8: "answer-first introductions",
  q9: "specific numbers and frameworks",
  q10: "/ai.txt",
}

const outcomeLabels: Record<string, string> = {
  first_mention: "getting mentioned by AI for the first time",
  benchmark: "understanding your competitive position",
  citations: "getting actual URL citations from AI",
  strategy: "building a complete AI visibility strategy",
}

function ResultsContent() {
  const params = useSearchParams()
  const score = parseFloat(params.get("score") || "0")
  const name = params.get("name") || "there"
  const q12 = params.get("q12") || ""
  const q14 = params.get("q14") || ""

  // Determine tier
  const tier = score >= 8 ? "high" : score >= 5 ? "medium" : "low"
  const scorePercent = Math.round((score / 10) * 100)

  // Find passes and fails
  const passes: string[] = []
  const fails: string[] = []
  Object.keys(bestPracticeLabels).forEach((key) => {
    const val = parseFloat(params.get(key) || "0")
    if (val >= 1) passes.push(bestPracticeLabels[key])
    else if (val === 0) fails.push(bestPracticeLabels[key])
  })

  // Dynamic headline
  const headlines: Record<string, string> = {
    high: `Congratulations, ${name}. Your AI infrastructure is strong.`,
    medium: `Well done, ${name}. Strong foundations, but room to improve.`,
    low: `${name}, you have significant room to improve. Here's your plan.`,
  }

  // Tier colors
  const tierColors: Record<string, string> = {
    high: "text-teal",
    medium: "text-yellow-400",
    low: "text-red-400",
  }

  const tierLabels: Record<string, string> = {
    high: "AI-READY",
    medium: "FOUNDATION READY",
    low: "NOT AI-READY",
  }

  // CTA based on Q14
  const ctaConfig: Record<string, { label: string; description: string; href: string }> = {
    done_for_you: {
      label: "Book a Strategy Call",
      description: "Get a full audit with a personalized implementation roadmap and 30-min call.",
      href: "#pricing",
    },
    consult: {
      label: "Book a Strategy Call",
      description: "30 minutes with an AI visibility expert to map your next steps.",
      href: "#pricing",
    },
    report: {
      label: "Get Your Quick Report ($147)",
      description: "Full AI visibility and citability test with 20 real queries across 3 platforms.",
      href: "#pricing",
    },
    diy: {
      label: "Download the Free Checklist",
      description: "A step-by-step guide to fix the gaps we found, starting with the highest-impact items.",
      href: "/",
    },
  }

  const cta = ctaConfig[q14] || ctaConfig.diy

  return (
    <main className="min-h-screen bg-background text-foreground px-4 py-16">
      <div className="max-w-2xl mx-auto flex flex-col gap-10">

        {/* Big Reveal */}
        <section className="text-center flex flex-col items-center gap-6">
          <div className="inline-flex items-center gap-2 border border-teal/30 bg-teal/5 rounded-full px-4 py-1.5">
            <span className="font-mono text-xs text-teal tracking-widest uppercase">
              Your Results
            </span>
          </div>

          {/* Score visualization */}
          <div className="relative w-40 h-40 flex items-center justify-center">
            <svg className="absolute inset-0 w-full h-full -rotate-90" viewBox="0 0 120 120">
              <circle
                cx="60"
                cy="60"
                r="52"
                fill="none"
                stroke="var(--border)"
                strokeWidth="8"
              />
              <circle
                cx="60"
                cy="60"
                r="52"
                fill="none"
                stroke={tier === "high" ? "var(--teal)" : tier === "medium" ? "#facc15" : "#f87171"}
                strokeWidth="8"
                strokeDasharray={`${scorePercent * 3.27} 327`}
                strokeLinecap="round"
                className="transition-all duration-1000"
              />
            </svg>
            <div className="text-center">
              <span className={cn("font-mono text-3xl font-bold", tierColors[tier])}>
                {score.toFixed(1)}
              </span>
              <span className="block font-mono text-xs text-muted-foreground">/10</span>
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

          <h1 className="text-2xl sm:text-3xl font-semibold text-foreground text-balance leading-snug">
            {headlines[tier]}
          </h1>
        </section>

        {/* Three Insights */}
        <section className="flex flex-col gap-4">
          <h2 className="font-mono text-xs text-teal tracking-widest uppercase">
            Your 3 Key Insights
          </h2>

          {/* Insight 1: Strongest area */}
          <div className="border border-border bg-navy-surface rounded-lg p-5 flex gap-4">
            <TrendingUp className="w-5 h-5 text-teal flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-foreground mb-1">What you're doing right</p>
              <p className="text-sm text-muted-foreground">
                {passes.length > 0
                  ? `You have ${passes.slice(0, 3).join(", ")}${passes.length > 3 ? `, and ${passes.length - 3} more` : ""} in place. ${passes.length >= 7 ? "That puts you ahead of 90% of sites we audit." : "That's a solid start."}`
                  : "You're taking this assessment, which means you're already ahead of most. Let's build from here."
                }
              </p>
            </div>
          </div>

          {/* Insight 2: Biggest gap */}
          <div className="border border-border bg-navy-surface rounded-lg p-5 flex gap-4">
            <AlertTriangle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-foreground mb-1">Your biggest gap</p>
              <p className="text-sm text-muted-foreground">
                {fails.length > 0
                  ? `You're missing ${fails.slice(0, 3).join(", ")}${fails.length > 3 ? `, and ${fails.length - 3} more` : ""}. ${fails.includes("/llms.txt") || fails.includes("/ai.txt") ? "These AI-specific surfaces are what separate visible sites from invisible ones." : "These are foundational signals that AI crawlers look for."}`
                  : "No major gaps detected. Your focus should be on content quality and monitoring."
                }
              </p>
            </div>
          </div>

          {/* Insight 3: Connection to goal */}
          <div className="border border-border bg-navy-surface rounded-lg p-5 flex gap-4">
            <Target className="w-5 h-5 text-teal flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-foreground mb-1">How this connects to your goal</p>
              <p className="text-sm text-muted-foreground">
                {q12 && outcomeLabels[q12]
                  ? `You said your priority is ${outcomeLabels[q12]}. ${
                      tier === "high"
                        ? "Your infrastructure supports this. The next step is a targeted strategy to convert infrastructure into actual citations."
                        : tier === "medium"
                          ? `Fixing your ${fails.length > 0 ? fails[0] : "gaps"} is the highest-leverage move toward this goal.`
                          : "The foundation needs to come first. AI systems can't cite what they can't crawl."
                    }`
                  : "Getting your infrastructure right is step one. AI visibility follows from a strong technical foundation."
                }
              </p>
            </div>
          </div>
        </section>

        {/* Next Steps CTA */}
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

        {/* Detailed breakdown */}
        <section>
          <h2 className="font-mono text-xs text-muted-foreground tracking-widest uppercase mb-4">
            Full Breakdown
          </h2>
          <div className="border border-border rounded-lg overflow-hidden">
            {Object.entries(bestPracticeLabels).map(([key, label], i) => {
              const val = parseFloat(params.get(key) || "0")
              const passed = val >= 1
              const partial = val > 0 && val < 1
              return (
                <div
                  key={key}
                  className={cn(
                    "flex items-center justify-between px-5 py-3 border-b border-border/60",
                    i % 2 === 0 ? "bg-background/60" : "bg-navy-surface/60"
                  )}
                >
                  <span className="text-sm text-foreground">{label}</span>
                  <span
                    className={cn(
                      "font-mono text-xs px-2 py-0.5 rounded border",
                      passed && "text-teal border-teal/30 bg-teal/10",
                      partial && "text-yellow-400 border-yellow-400/30 bg-yellow-400/10",
                      !passed && !partial && "text-red-400 border-red-400/30 bg-red-400/10"
                    )}
                  >
                    {passed ? "PASS" : partial ? "PARTIAL" : "FAIL"}
                  </span>
                </div>
              )
            })}
          </div>
        </section>

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
