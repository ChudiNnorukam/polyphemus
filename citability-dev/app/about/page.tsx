import type { Metadata } from 'next'
import { ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Nav } from '@/components/nav'
import { Footer } from '@/components/footer'

export const metadata: Metadata = {
  title: 'About - citability.dev',
  description: 'Chudi Nnorukam built citability.dev to measure what traditional SEO tools miss: whether AI systems like ChatGPT, Perplexity, and Claude can find, recommend, and cite your website. Learn about the AI Visibility Readiness (AVR) framework.',
  alternates: {
    canonical: '/about',
  },
  openGraph: {
    title: 'About citability.dev',
    description: 'The founder and mission behind AI visibility auditing',
    url: 'https://citability.dev/about',
    type: 'website',
  },
}

export default function AboutPage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <Nav />

      {/* Hero section with scroll offset */}
      <div className="pt-32 pb-16 px-4">
        <div className="max-w-4xl mx-auto">
          <h1 className="text-5xl md:text-6xl font-semibold text-foreground leading-tight mb-8">
            About citability
          </h1>
          <p className="text-lg text-muted-foreground max-w-2xl">
            Why I built a tool to measure what Ahrefs and Semrush will never see: whether AI systems can find you, recommend you, and cite you.
          </p>
        </div>
      </div>

      {/* Main content */}
      <div className="px-4 py-16">
        <div className="max-w-4xl mx-auto space-y-16">
          {/* Founder section */}
          <section>
            <h2 className="text-2xl font-semibold text-foreground mb-6">Who</h2>
            <div className="space-y-4 text-foreground/90 leading-relaxed">
              <p>
                I'm <span className="font-semibold">Chudi Nnorukam</span>, an AI-Visible Web Architect. For 8+ years, I've built systems that help websites rank in traditional search. But starting in 2024, I noticed something that nobody was talking about: the explosion of AI search changed the ranking game overnight.
              </p>
              <p>
                ChatGPT, Perplexity, Claude, and hundreds of smaller AI research assistants are now the first place millions of people look for answers. They don't use Google's ranking algorithm. They don't respect robots.txt. They have their own crawlers, their own indexing rules, and their own citation preferences.
              </p>
              <p>
                And almost nobody is measuring whether AI systems can find them.
              </p>
            </div>
          </section>

          {/* What section */}
          <section>
            <h2 className="text-2xl font-semibold text-foreground mb-6">What</h2>
            <div className="space-y-4 text-foreground/90 leading-relaxed">
              <p>
                I built <span className="font-semibold">citability.dev</span> to measure that gap. The platform runs <strong>10 verified infrastructure checks</strong>, each backed by official documentation from OpenAI, Anthropic, Perplexity, and the AI Improvement Program (aiip.dev).
              </p>
              <p>
                The checks cover:
              </p>
              <ul className="space-y-2 ml-4">
                <li>
                  <span className="font-mono text-sm text-muted-foreground">llms.txt</span>
                  <span className="text-foreground/80"> – Can AI systems find your AI metadata?</span>
                </li>
                <li>
                  <span className="font-mono text-sm text-muted-foreground">ai.txt</span>
                  <span className="text-foreground/80"> – Is your AI opt-in policy declared?</span>
                </li>
                <li>
                  <span className="font-mono text-sm text-muted-foreground">.well-known/ai.json</span>
                  <span className="text-foreground/80"> – Can AI crawlers parse your structured metadata?</span>
                </li>
                <li>
                  <span className="font-mono text-sm text-muted-foreground">Schema.org markup</span>
                  <span className="text-foreground/80"> – Is your content tagged for AI understanding?</span>
                </li>
                <li>
                  <span className="font-mono text-sm text-muted-foreground">Crawlability</span>
                  <span className="text-foreground/80"> – Can AI bots actually reach your content?</span>
                </li>
                <li>
                  <span className="font-mono text-sm text-muted-foreground">Citation infrastructure</span>
                  <span className="text-foreground/80"> – When AI systems mention you, are URLs properly formatted?</span>
                </li>
              </ul>
              <p className="mt-4">
                Each check returns a pass/fail plus a link to the official spec. No guessing. No vague best practices.
              </p>
            </div>
          </section>

          {/* Why section */}
          <section>
            <h2 className="text-2xl font-semibold text-foreground mb-6">Why</h2>
            <div className="bg-navy-elevated border border-border rounded-lg p-6 my-6">
              <blockquote className="italic text-foreground/90 leading-relaxed">
                "I built citability.dev because I noticed a gap: companies invest heavily in SEO but have zero infrastructure for AI search. Traditional SEO tools measure Google rankings. They don't measure whether ChatGPT, Perplexity, or Claude can find you."
              </blockquote>
            </div>
            <div className="space-y-4 text-foreground/90 leading-relaxed">
              <p>
                In January 2024, I started seeing companies panic about AI. They asked: "Will ChatGPT cite us?" "Can Claude find our documentation?" "How do we get mentioned in Perplexity results?"
              </p>
              <p>
                The answer was always: we don't know. Ahrefs can't measure it. Semrush can't measure it. Google Search Console doesn't show it. And there was no framework to diagnose the problem.
              </p>
              <p>
                So I built one. I studied how Claude, ChatGPT, and Perplexity crawl the web. I read the official documentation. I tested what works and what doesn't. And I codified it into 10 checks that anyone can run against their website.
              </p>
            </div>
          </section>

          {/* Open source section */}
          <section>
            <h2 className="text-2xl font-semibold text-foreground mb-6">Open Source</h2>
            <div className="space-y-4 text-foreground/90 leading-relaxed">
              <p>
                The <strong>AI Visibility Readiness (AVR) Framework</strong> is fully open source and available on GitHub under the Apache 2.0 license. The code powers every scan on citability.dev.
              </p>
              <p>
                If you want to run audits on your own infrastructure, integrate with your CI/CD pipeline, or fork and extend the framework, everything is available. No lock-in. No proprietary black box.
              </p>
              <a
                href="https://github.com/ChudiNnorukam/ai-visibility-readiness"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 font-mono text-sm text-teal hover:text-teal-dim transition-colors"
              >
                View on GitHub
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </section>

          {/* Links section */}
          <section>
            <h2 className="text-2xl font-semibold text-foreground mb-6">Links</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <a
                href="https://linkedin.com/in/chudi-nnorukam"
                target="_blank"
                rel="noopener noreferrer"
                className="p-4 border border-border rounded-lg hover:border-teal/50 hover:bg-teal/5 transition-all group"
              >
                <div className="font-mono text-sm text-teal mb-2">LinkedIn</div>
                <div className="text-foreground/80 text-sm group-hover:text-foreground transition-colors">
                  Follow for updates on AI visibility trends
                </div>
              </a>
              <a
                href="https://github.com/ChudiNnorukam"
                target="_blank"
                rel="noopener noreferrer"
                className="p-4 border border-border rounded-lg hover:border-teal/50 hover:bg-teal/5 transition-all group"
              >
                <div className="font-mono text-sm text-teal mb-2">GitHub</div>
                <div className="text-foreground/80 text-sm group-hover:text-foreground transition-colors">
                  Explore all open source projects
                </div>
              </a>
              <a
                href="https://chudi.dev"
                target="_blank"
                rel="noopener noreferrer"
                className="p-4 border border-border rounded-lg hover:border-teal/50 hover:bg-teal/5 transition-all group"
              >
                <div className="font-mono text-sm text-teal mb-2">chudi.dev</div>
                <div className="text-foreground/80 text-sm group-hover:text-foreground transition-colors">
                  Personal site and writing
                </div>
              </a>
              <a
                href="mailto:hello@citability.dev"
                className="p-4 border border-border rounded-lg hover:border-teal/50 hover:bg-teal/5 transition-all group"
              >
                <div className="font-mono text-sm text-teal mb-2">Email</div>
                <div className="text-foreground/80 text-sm group-hover:text-foreground transition-colors">
                  hello@citability.dev
                </div>
              </a>
            </div>
          </section>

          {/* CTA section */}
          <section className="pt-8">
            <div className="bg-navy-elevated border border-border rounded-lg p-8 text-center space-y-6">
              <div>
                <h2 className="text-2xl font-semibold text-foreground mb-2">
                  Ready to check your AI visibility?
                </h2>
                <p className="text-foreground/80">
                  Run the free scan on your website and get 10 verified infrastructure checks backed by official documentation.
                </p>
              </div>
              <a href="/assess">
                <Button
                  className="h-12 bg-teal text-primary-foreground hover:bg-teal-dim font-semibold font-mono"
                >
                  Run the Free Scan
                </Button>
              </a>
              <p className="font-mono text-xs text-muted-foreground">
                Takes 3 minutes. No credit card required.
              </p>
            </div>
          </section>
        </div>
      </div>

      <Footer />

      {/* Person schema (JSON-LD) */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'Person',
            name: 'Chudi Nnorukam',
            url: 'https://chudi.dev',
            sameAs: [
              'https://linkedin.com/in/chudi-nnorukam',
              'https://github.com/ChudiNnorukam',
              'https://chudi.dev',
            ],
            jobTitle: 'AI-Visible Web Architect',
            description: 'Founder of citability.dev. Built the AI Visibility Readiness (AVR) Framework to measure whether AI systems can find, recommend, and cite websites.',
            knowsAbout: [
              'AI Visibility',
              'Web Architecture',
              'Search Engine Optimization',
              'Structured Data',
              'AI Crawlers',
            ],
          }),
        }}
      />
    </main>
  )
}
