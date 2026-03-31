"use client"

import { Eye, ThumbsUp, Link2, ArrowRight } from "lucide-react"

const pillars = [
  {
    step: "01",
    icon: Eye,
    title: "AI Visibility",
    question: "Can AI systems find you?",
    description:
      "We test whether ChatGPT, Perplexity, and Claude recognize your brand when asked directly.",
    tag: "FOUNDATION",
  },
  {
    step: "02",
    icon: ThumbsUp,
    title: "AI Recommendability",
    question: "Does AI suggest you?",
    description:
      "When someone asks AI for tools or resources in your space, does it mention you? We test this with real queries.",
    tag: "INTERMEDIATE",
  },
  {
    step: "03",
    icon: Link2,
    title: "AI Citability",
    question: "Does AI link to you?",
    description:
      "The highest bar: does the AI include your URL as a source? We check across 3 platforms with 20 real queries.",
    tag: "ADVANCED",
  },
]

export function ThreePillars() {
  return (
    <section className="py-24 px-4">
      <div className="max-w-6xl mx-auto">
        {/* Section header */}
        <div className="mb-16 text-center">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">The Framework</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground text-balance">
            Three Metrics That Actually Matter
          </h2>
        </div>

        {/* Cards with connecting arrows */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-0 md:gap-0 relative">
          {pillars.map((pillar, i) => {
            const Icon = pillar.icon
            return (
              <div key={pillar.step} className="relative flex flex-col md:flex-row">
                {/* Card */}
                <div className="flex-1 border border-border bg-navy-surface rounded-lg p-6 md:p-8 flex flex-col gap-5 hover:border-teal/30 transition-colors duration-300 group">
                  {/* Top row */}
                  <div className="flex items-start justify-between">
                    <div className="w-10 h-10 rounded-md bg-teal/10 border border-teal/20 flex items-center justify-center group-hover:bg-teal/15 transition-colors">
                      <Icon className="w-5 h-5 text-teal" />
                    </div>
                    <span className="font-mono text-xs text-muted-foreground border border-border rounded px-2 py-1">
                      {pillar.tag}
                    </span>
                  </div>

                  {/* Step */}
                  <div>
                    <span className="font-mono text-xs text-muted-foreground">{pillar.step}</span>
                    <h3 className="mt-1 text-xl font-bold text-foreground">{pillar.title}</h3>
                    <p className="mt-1 font-mono text-sm text-teal">{pillar.question}</p>
                  </div>

                  {/* Description */}
                  <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                    {pillar.description}
                  </p>

                  {/* Bottom accent bar */}
                  <div className="h-px w-full bg-border group-hover:bg-teal/30 transition-colors" />
                </div>

                {/* Arrow connector */}
                {i < pillars.length - 1 && (
                  <>
                    {/* Mobile: down arrow */}
                    <div className="flex md:hidden justify-center py-3">
                      <ArrowRight className="w-5 h-5 text-muted-foreground rotate-90" />
                    </div>
                    {/* Desktop: right arrow */}
                    <div className="hidden md:flex items-center justify-center w-8 flex-shrink-0 z-10">
                      <ArrowRight className="w-5 h-5 text-teal/50" />
                    </div>
                  </>
                )}
              </div>
            )
          })}
        </div>

        {/* Footnote */}
        <p className="mt-10 text-center font-mono text-xs text-muted-foreground border border-border bg-navy-surface rounded-md px-6 py-3 max-w-2xl mx-auto">
          Each metric is tested with real API calls to real AI platforms.{" "}
          <span className="text-foreground">No made-up scores. No proxy signals.</span>
        </p>
      </div>
    </section>
  )
}
