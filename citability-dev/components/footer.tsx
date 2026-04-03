"use client"

import { ExternalLink } from "lucide-react"

const links = [
  { label: "Methodology", href: "/methodology" },
  { label: "About", href: "/about" },
  { label: "GitHub", href: "https://github.com/ChudiNnorukam/ai-visibility-readiness" },
  { label: "LinkedIn", href: "https://linkedin.com/in/chudi-nnorukam" },
  { label: "Privacy", href: "/privacy" },
  { label: "Terms", href: "/terms" },
  { label: "Contact", href: "mailto:hello@citability.dev" },
]

export function Footer() {
  return (
    <footer className="border-t border-border bg-navy-elevated">
      <div className="max-w-6xl mx-auto px-4 py-10 flex flex-col md:flex-row items-center justify-between gap-6">
        {/* Brand */}
        <div className="flex flex-col items-center md:items-start gap-1">
          <a
            href="https://chudi.dev"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            Built by{" "}
            <span className="font-mono text-foreground">Chudi Nnorukam</span>
            <ExternalLink className="w-3 h-3" />
          </a>
          <p className="font-mono text-xs text-muted-foreground/60">
            Powered by the AI Visibility Readiness (AVR) Framework v1.0
          </p>
        </div>

        {/* Links */}
        <nav className="flex items-center gap-6">
          {links.map((link) => (
            <a
              key={link.label}
              href={link.href}
              className="font-mono text-xs text-muted-foreground hover:text-teal transition-colors"
            >
              {link.label}
            </a>
          ))}
        </nav>
      </div>
    </footer>
  )
}
