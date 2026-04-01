"use client"

import { Check, ArrowRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const tiers = [
  {
    name: "Free Scan",
    price: "$0",
    description: "Infrastructure check in 60 seconds.",
    cta: "Scan Now",
    href: "/assess",
    featured: false,
    features: [
      "10 infrastructure + content checks",
      "SEO foundation score",
      "Citation-readiness score",
      "Instant results",
    ],
  },
  {
    name: "Quick Report",
    price: "$147",
    description: "Full AI visibility picture with live data.",
    cta: "Get Report",
    href: "mailto:hello@citability.dev?subject=Quick%20Report%20Request&body=I%27d%20like%20to%20order%20a%20Quick%20Report%20for%20my%20website.",
    featured: true,
    features: [
      "Everything in Free",
      "Live AI visibility test (20 queries)",
      "Live AI citability test (20 queries)",
      "Recommendability analysis",
      "PDF report with scores + benchmarks",
    ],
  },
  {
    name: "Full Audit + Strategy Call",
    price: "$497",
    description: "Complete audit with roadmap and direct support.",
    cta: "Book Audit",
    href: "mailto:hello@citability.dev?subject=Full%20Audit%20%2B%20Strategy%20Call&body=I%27d%20like%20to%20book%20a%20Full%20Audit%20with%20Strategy%20Call.",
    featured: false,
    features: [
      "Everything in Quick Report",
      "30-min strategy call",
      "Written implementation roadmap (5–7 actions)",
      "Competitor comparison (your site vs 2 competitors)",
      "Priority support for 30 days",
    ],
  },
]

export function Pricing() {
  return (
    <section id="pricing" className="py-24 px-4 bg-navy-surface/40">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-16 text-center">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Pricing</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground text-balance">
            Start Free. Go Deep When Ready.
          </h2>
        </div>

        {/* Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {tiers.map((tier) => (
            <div
              key={tier.name}
              className={cn(
                "relative flex flex-col gap-6 p-7 rounded-lg border transition-colors duration-300",
                tier.featured
                  ? "border-teal/50 bg-teal/5 shadow-[0_0_40px_oklch(0.78_0.18_192_/_0.08)]"
                  : "border-border bg-navy-surface hover:border-teal/20",
              )}
            >
              {tier.featured && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                  <span className="font-mono text-xs bg-teal text-primary-foreground px-3 py-1 rounded-full">
                    MOST POPULAR
                  </span>
                </div>
              )}

              {/* Price header */}
              <div>
                <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider">
                  {tier.name}
                </p>
                <div className="mt-2 flex items-baseline gap-2">
                  <span
                    className={cn(
                      "font-mono text-4xl font-bold",
                      tier.featured ? "text-teal" : "text-foreground",
                    )}
                  >
                    {tier.price}
                  </span>
                </div>
                <p className="mt-1.5 text-sm text-muted-foreground">{tier.description}</p>
              </div>

              {/* Divider */}
              <div className="h-px bg-border" />

              {/* Features */}
              <ul className="flex flex-col gap-3 flex-1">
                {tier.features.map((feature) => (
                  <li key={feature} className="flex items-start gap-2.5 text-sm">
                    <Check
                      className={cn(
                        "w-4 h-4 mt-0.5 flex-shrink-0",
                        tier.featured ? "text-teal" : "text-muted-foreground",
                      )}
                    />
                    <span className="text-foreground/80">{feature}</span>
                  </li>
                ))}
              </ul>

              {/* CTA */}
              <a href={tier.href}>
                <Button
                  className={cn(
                    "w-full flex items-center gap-2 font-semibold",
                    tier.featured
                      ? "bg-teal text-primary-foreground hover:bg-teal-dim"
                      : "bg-secondary text-foreground hover:bg-secondary/80",
                  )}
                >
                  {tier.cta}
                  <ArrowRight className="w-4 h-4" />
                </Button>
              </a>
            </div>
          ))}
        </div>

        {/* Trust + Implementation sprint */}
        <p className="mt-6 text-center text-sm text-muted-foreground">
          One-time payment. No subscription. Results delivered within 48 hours.
        </p>
        <p className="mt-2 text-center text-xs text-muted-foreground font-mono">
          Implementation Sprint available for{" "}
          <span className="text-foreground">$1,997</span>{" "}
          (4 weeks of guided implementation)
        </p>
      </div>
    </section>
  )
}
