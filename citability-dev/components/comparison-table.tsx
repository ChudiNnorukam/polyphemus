"use client"

import { cn } from "@/lib/utils"

type StatusBadgeProps = {
  label: string
  variant: "ready" | "partial" | "not-ready" | "high" | "low" | "na"
}

function StatusBadge({ label, variant }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "font-mono text-xs px-2 py-0.5 rounded border",
        variant === "ready" && "text-teal border-teal/30 bg-teal/10",
        variant === "partial" && "text-yellow-400 border-yellow-400/30 bg-yellow-400/10",
        variant === "not-ready" && "text-red-400 border-red-400/30 bg-red-400/10",
        variant === "high" && "text-teal border-teal/30 bg-teal/10",
        variant === "low" && "text-muted-foreground border-border bg-transparent",
        variant === "na" && "text-muted-foreground/50 border-border/50 bg-transparent",
      )}
    >
      {label}
    </span>
  )
}

const rows = [
  {
    site: "ahrefs.com",
    infra: { label: "FOUNDATION-READY", variant: "partial" as const },
    visibility: { label: "100% HIGH", variant: "high" as const },
    citability: { label: "5% PARTIAL", variant: "partial" as const },
    overall: "Visible, rarely cited",
  },
  {
    site: "semrush.com",
    infra: { label: "FOUNDATION-READY", variant: "partial" as const },
    visibility: { label: "—", variant: "na" as const },
    citability: { label: "—", variant: "na" as const },
    overall: "Infrastructure gaps",
  },
  {
    site: "chudi.dev",
    infra: { label: "FOUNDATION-STRONG", variant: "ready" as const },
    visibility: { label: "29% PARTIAL", variant: "partial" as const },
    citability: { label: "0% NOT CITED", variant: "low" as const },
    overall: "Visible, not yet cited",
  },
  {
    site: "reddit.com",
    infra: { label: "NOT-READY", variant: "not-ready" as const },
    visibility: { label: "—", variant: "na" as const },
    citability: { label: "—", variant: "na" as const },
    overall: "Missing basics",
  },
  {
    site: "medium.com",
    infra: { label: "NOT-READY", variant: "not-ready" as const },
    visibility: { label: "—", variant: "na" as const },
    citability: { label: "—", variant: "na" as const },
    overall: "Missing basics",
  },
  {
    site: "x.com",
    infra: { label: "NOT-READY", variant: "not-ready" as const },
    visibility: { label: "—", variant: "na" as const },
    citability: { label: "—", variant: "na" as const },
    overall: "Missing basics",
  },
]

export function ComparisonTable() {
  return (
    <section id="comparison" className="py-24 px-4 bg-navy-surface/40">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-12 text-center">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Benchmark Data</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground text-balance">
            How Do the Biggest Sites Score?
          </h2>
        </div>

        {/* Table */}
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-navy-elevated">
                <th className="text-left px-5 py-4 font-mono text-xs text-muted-foreground uppercase tracking-wider">
                  Site
                </th>
                <th className="text-left px-5 py-4 font-mono text-xs text-muted-foreground uppercase tracking-wider">
                  AI Infrastructure
                </th>
                <th className="text-left px-5 py-4 font-mono text-xs text-muted-foreground uppercase tracking-wider">
                  AI Visibility
                </th>
                <th className="text-left px-5 py-4 font-mono text-xs text-muted-foreground uppercase tracking-wider">
                  AI Citability
                </th>
                <th className="text-left px-5 py-4 font-mono text-xs text-muted-foreground uppercase tracking-wider">
                  Overall
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={row.site}
                  className={cn(
                    "border-b border-border/60 transition-colors hover:bg-teal/5 cursor-default",
                    i % 2 === 0 ? "bg-background/60" : "bg-navy-surface/60",
                  )}
                >
                  <td className="px-5 py-4">
                    <span className="font-mono text-sm text-foreground">{row.site}</span>
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge label={row.infra.label} variant={row.infra.variant} />
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge label={row.visibility.label} variant={row.visibility.variant} />
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge label={row.citability.label} variant={row.citability.variant} />
                  </td>
                  <td className="px-5 py-4 text-muted-foreground text-xs">{row.overall}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Footnote */}
        <p className="mt-6 text-center text-sm text-muted-foreground">
          Even{" "}
          <span className="font-mono text-foreground">DA 90+</span> sites fail our AI readiness checks.{" "}
          Domain authority alone doesn&apos;t mean AI visibility.
        </p>
      </div>
    </section>
  )
}
