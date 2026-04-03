"use client"

import { useState, useEffect } from "react"
import { Star, Menu, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const navLinks = [
  { label: "How It Works", href: "#how-it-works" },
  { label: "Methodology", href: "/methodology" },
  { label: "Benchmark", href: "#comparison" },
  { label: "Pricing", href: "#pricing" },
]

export function Nav() {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20)
    window.addEventListener("scroll", onScroll)
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  const handleScroll = (e: React.MouseEvent<HTMLAnchorElement>, href: string) => {
    if (!href.startsWith("#")) return // Allow normal navigation for non-hash links
    e.preventDefault()
    const id = href.replace("#", "")
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" })
  }

  return (
    <>
    <header
      className={cn(
        "fixed top-0 left-0 right-0 z-50 transition-all duration-300",
        scrolled
          ? "border-b border-border bg-background/95 backdrop-blur-md"
          : "bg-transparent",
      )}
    >
      <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
        {/* Logo */}
        <a href="#" className="font-mono text-sm font-bold text-foreground hover:text-teal transition-colors">
          citability<span className="text-teal">.dev</span>
        </a>

        {/* Nav links (desktop) */}
        <nav className="hidden md:flex items-center gap-6">
          {navLinks.map((link) => (
            <a
              key={link.label}
              href={link.href}
              onClick={(e) => handleScroll(e, link.href)}
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {link.label}
            </a>
          ))}
        </nav>

        {/* Mobile hamburger */}
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="md:hidden p-2 text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Toggle menu"
        >
          {menuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>

        {/* CTAs */}
        <div className="hidden md:flex items-center gap-3">
          <a
            href="https://github.com/ChudiNnorukam/ai-visibility-readiness"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden sm:inline-flex items-center gap-1.5 border border-border bg-navy-surface hover:bg-navy-elevated rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <Star className="w-3.5 h-3.5" />
            <span className="font-mono">Star on GitHub</span>
          </a>
          <a href="/assess">
            <Button
              size="sm"
              className="bg-teal text-primary-foreground hover:bg-teal-dim font-semibold font-mono text-xs"
            >
              Free Scan
            </Button>
          </a>
        </div>
      </div>
    </header>

      {/* Mobile menu */}
      {menuOpen && (
        <div className="fixed top-16 left-0 right-0 z-40 md:hidden border-b border-border bg-background/95 backdrop-blur-md">
          <div className="flex flex-col px-4 py-4 gap-3">
            {navLinks.map((link) => (
              <a
                key={link.label}
                href={link.href}
                onClick={(e) => {
                  handleScroll(e, link.href)
                  setMenuOpen(false)
                }}
                className="text-sm text-muted-foreground hover:text-foreground transition-colors py-2"
              >
                {link.label}
              </a>
            ))}
            <a
              href="https://github.com/ChudiNnorukam/ai-visibility-readiness"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors py-2"
            >
              <Star className="w-3.5 h-3.5" />
              Star on GitHub
            </a>
            <a href="/assess" className="mt-1">
              <Button className="w-full bg-teal text-primary-foreground hover:bg-teal-dim font-semibold font-mono text-xs">
                Free Scan
              </Button>
            </a>
          </div>
        </div>
      )}
    </>
  )
}
