"use client"

import { useEffect, useRef, type ReactNode } from "react"
import { Nav } from "@/components/nav"
import { Hero } from "@/components/hero"
import { ThreePillars } from "@/components/three-pillars"
import { ComparisonTable } from "@/components/comparison-table"
import { HowItWorks } from "@/components/how-it-works"
import { Pricing } from "@/components/pricing"
import { Methodology } from "@/components/methodology"
import { Footer } from "@/components/footer"

function FadeInSection({ children, id }: { children: ReactNode; id?: string }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.style.opacity = "1"
          el.style.transform = "translateY(0)"
          observer.unobserve(el)
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -40px 0px" },
    )

    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div
      id={id}
      ref={ref}
      style={{
        opacity: 0,
        transform: "translateY(24px)",
        transition: "opacity 0.6s ease, transform 0.6s ease",
      }}
    >
      {children}
    </div>
  )
}

export default function Page() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <Nav />

      <Hero />

      <FadeInSection>
        <ThreePillars />
      </FadeInSection>

      <FadeInSection id="comparison">
        <ComparisonTable />
      </FadeInSection>

      <FadeInSection id="how-it-works">
        <HowItWorks />
      </FadeInSection>

      <FadeInSection id="pricing">
        <Pricing />
      </FadeInSection>

      <FadeInSection>
        <Methodology />
      </FadeInSection>

      <Footer />
    </main>
  )
}
