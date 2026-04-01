"use client"

import { Globe, FileText, Wrench } from "lucide-react"

const steps = [
  {
    number: "01",
    icon: Globe,
    title: "Enter your domain",
    description:
      "Our free scan checks 10 infrastructure and content-readiness signals in 60 seconds.",
    detail: "robots.txt · sitemap.xml · answer-first intros · freshness · structured data · and more",
  },
  {
    number: "02",
    icon: FileText,
    title: "Get your AI Visibility Report",
    description:
      "We query ChatGPT, Perplexity, and Claude with 20 real search queries about your brand and topics.",
    detail: "Brand queries · category queries · comparison queries · recommendation queries",
  },
  {
    number: "03",
    icon: Wrench,
    title: "See exactly what to fix",
    description:
      'Every finding comes with a specific action. Not "improve your SEO." More like "rewrite the intro to answer the query in the first 100 words."',
    detail: "Prioritized by impact · labeled by effort · benchmarked against competitors",
  },
]

export function HowItWorks() {
  return (
    <section className="py-24 px-4">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-16 text-center">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Process</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground text-balance">
            How It Works
          </h2>
        </div>

        {/* Steps */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {steps.map((step) => {
            const Icon = step.icon
            return (
              <div
                key={step.number}
                className="relative flex flex-col gap-5 p-6 rounded-lg border border-border bg-navy-surface hover:border-teal/30 transition-colors duration-300 group"
              >
                {/* Number + icon */}
                <div className="flex items-center justify-between">
                  <span className="font-mono text-4xl font-bold text-teal/20 group-hover:text-teal/30 transition-colors select-none">
                    {step.number}
                  </span>
                  <div className="w-10 h-10 rounded-md bg-teal/10 border border-teal/20 flex items-center justify-center">
                    <Icon className="w-5 h-5 text-teal" />
                  </div>
                </div>

                {/* Content */}
                <div>
                  <h3 className="text-lg font-bold text-foreground">{step.title}</h3>
                  <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                    {step.description}
                  </p>
                </div>

                {/* Detail tag */}
                <div className="mt-auto pt-4 border-t border-border">
                  <p className="font-mono text-xs text-muted-foreground/70">{step.detail}</p>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
