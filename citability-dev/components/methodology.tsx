"use client"

import { ShieldCheck, ExternalLink } from "lucide-react"

const points = [
  {
    tag: "[VERIFIABLE]",
    text: 'Every check is labeled',
    extra: 'or [BEST-EFFORT] so you know exactly what you\'re getting.',
  },
  {
    tag: null,
    text: "85% of websites have zero AI-readable surfaces. No /llms.txt, no /ai.txt, no AI crawler policy.",
    extra: null,
  },
  {
    tag: null,
    text: "Even ahrefs.com (DA 92) is 100% AI-visible but only 5% AI-cited. Visibility and citations are different problems.",
    extra: null,
  },
  {
    tag: "[LOW / MODERATE / HIGH]",
    text: 'AI citation results vary by session. We label confidence:',
    extra: 'so you understand the variance.',
  },
]

export function Methodology() {
  return (
    <section className="py-24 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="mb-14 text-center">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Trust</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground text-balance">
            Why Our Scores Are Different
          </h2>
        </div>

        <div className="border border-border rounded-lg bg-navy-surface overflow-hidden">
          {/* Icon row */}
          <div className="flex items-center gap-3 px-6 py-4 border-b border-border bg-navy-elevated">
            <ShieldCheck className="w-5 h-5 text-teal" />
            <span className="font-mono text-sm text-foreground">
              AVR Framework v1.0 — Methodology
            </span>
          </div>

          {/* Points */}
          <ul className="flex flex-col divide-y divide-border">
            {points.map((point, i) => (
              <li key={i} className="px-6 py-5 flex items-start gap-4 hover:bg-teal/5 transition-colors">
                <div className="w-1.5 h-1.5 rounded-full bg-teal mt-2 flex-shrink-0" />
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {point.text}{" "}
                  {point.tag && (
                    <span className="font-mono text-xs text-teal bg-teal/10 border border-teal/20 rounded px-1.5 py-0.5">
                      {point.tag}
                    </span>
                  )}{" "}
                  {point.extra && <span>{point.extra}</span>}
                </p>
              </li>
            ))}
          </ul>

          {/* Footer link */}
          <div className="px-6 py-4 border-t border-border bg-navy-elevated">
            <a
              href="#"
              className="inline-flex items-center gap-1.5 font-mono text-xs text-teal hover:text-teal-dim transition-colors"
            >
              Read the full methodology
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </div>
    </section>
  )
}
