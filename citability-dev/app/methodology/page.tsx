import type { Metadata } from "next"
import { ShieldCheck, ExternalLink, Github, CheckCircle, AlertTriangle } from "lucide-react"

export const metadata: Metadata = {
  title: "Methodology - citability.dev",
  description:
    "How our AI visibility scan works. 10 checks, each labeled with evidence tier and explanation. No made-up scores.",
}

const checks = [
  {
    name: "robots.txt",
    tier: "VERIFIED",
    what: "Checks whether your robots.txt file exists and is accessible.",
    who: "All major crawlers: Googlebot, GPTBot (OpenAI), ClaudeBot (Anthropic), CCBot (Common Crawl), Meta-ExternalAgent.",
    why: "robots.txt is the universal protocol for communicating crawl permissions. Without it, crawlers may apply default behavior or skip your site entirely. AI-specific directives (allow/disallow GPTBot, ClaudeBot) are only possible if the file exists.",
    source: "https://platform.openai.com/docs/gptbot",
  },
  {
    name: "sitemap.xml",
    tier: "VERIFIED",
    what: "Checks whether a sitemap exists at your domain root.",
    who: "All search engines and AI crawlers use sitemaps for content discovery.",
    why: "Sitemaps tell crawlers which pages exist and when they were last updated. Without one, crawlers rely on link-following, which misses orphaned pages and provides no freshness signal.",
    source: "https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview",
  },
  {
    name: "Answer-First Content",
    tier: "VERIFIED",
    what: "Analyzes whether your homepage leads with a direct, extractable answer rather than generic marketing copy.",
    who: "Google (featured snippets, AI Overviews), Perplexity, ChatGPT browse mode.",
    why: "AI systems extract concise answers from pages. Content that buries the answer below navigation, hero images, or generic taglines is less likely to be selected for AI-generated responses.",
    source: "https://developers.google.com/search/docs/appearance/featured-snippets",
  },
  {
    name: "Content Freshness",
    tier: "VERIFIED",
    what: "Checks for date signals: published dates, modified dates, or dateModified in schema.",
    who: "Google (QDF algorithm), AI systems that prioritize recent content.",
    why: "Stale content without date signals gets deprioritized. Google's Query Deserves Freshness (QDF) algorithm and AI training pipelines both use recency as a quality signal.",
    source: "https://developers.google.com/search/docs/appearance/publication-dates",
  },
  {
    name: "Structured Data (JSON-LD)",
    tier: "VERIFIED",
    what: "Checks for JSON-LD structured data blocks on your homepage.",
    who: "Google, Bing, and AI systems parse JSON-LD to understand entities and page purpose.",
    why: "Schema markup (Organization, Article, FAQPage, HowTo) gives machines explicit context about your content. Pages with rich schema are more likely to generate rich results and be understood correctly by AI models.",
    source: "https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data",
  },
  {
    name: "Meta Description",
    tier: "VERIFIED",
    what: "Checks for a meta description tag with meaningful content (>10 characters).",
    who: "All search engines use it for snippet generation. AI systems use it as a page summary signal.",
    why: "The meta description is often the first text an AI system reads about your page. A missing or generic description means the AI must guess your page's purpose from the body content.",
    source: "https://developers.google.com/search/docs/appearance/snippet",
  },
  {
    name: "Canonical URL",
    tier: "VERIFIED",
    what: "Checks for a rel=canonical tag pointing to the authoritative version of the page.",
    who: "All search engines and AI training crawlers.",
    why: "Without a canonical, duplicate versions of your content (www vs non-www, HTTP vs HTTPS, query parameters) compete with each other. AI systems may cite the wrong version or split authority across duplicates.",
    source: "https://developers.google.com/search/docs/crawling-indexing/canonicalization",
  },
  {
    name: "HTTPS",
    tier: "VERIFIED",
    what: "Checks whether your site is served over HTTPS.",
    who: "Google (confirmed ranking signal since 2014), all AI crawlers that fetch content.",
    why: "HTTPS is a baseline trust signal. AI systems that fetch live content (ChatGPT browse mode, Perplexity) require HTTPS for secure retrieval. Non-HTTPS sites may be skipped or flagged.",
    source: "https://developers.google.com/search/docs/crawling-indexing/https",
  },
  {
    name: "Heading Hierarchy",
    tier: "VERIFIED",
    what: "Checks for at least one H1 tag and proper heading structure.",
    who: "Search engines use headings to understand content hierarchy. AI models use them to identify key topics.",
    why: "A clear H1 > H2 > H3 hierarchy helps AI systems extract the main topic and subtopics from your page. Pages without an H1 lack a clear primary topic signal.",
    source: "https://developers.google.com/search/docs/appearance/title-link",
  },
  {
    name: "Social Sharing Readiness",
    tier: "VERIFIED",
    what: "Checks for Open Graph tags (og:title, og:description, og:image).",
    who: "Social platforms (LinkedIn, Twitter/X, Facebook), AI systems that preview links.",
    why: "OG tags control how your page appears when shared. AI systems that browse the web use these as quick metadata signals. Missing OG tags mean your content previews are auto-generated and often wrong.",
    source: "https://ogp.me/",
  },
]

export default function MethodologyPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <header className="border-b border-border bg-background/95 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-4xl mx-auto px-4 h-16 flex items-center justify-between">
          <a href="/" className="font-mono text-sm font-bold text-foreground hover:text-teal transition-colors">
            citability<span className="text-teal">.dev</span>
          </a>
          <a
            href="/assess"
            className="bg-teal text-primary-foreground hover:opacity-90 font-semibold font-mono text-xs px-4 py-2 rounded-md transition-opacity"
          >
            Free Scan
          </a>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-16">
        {/* Header */}
        <div className="mb-12">
          <span className="font-mono text-xs text-teal tracking-widest uppercase">Methodology</span>
          <h1 className="mt-3 text-3xl sm:text-4xl font-bold text-foreground">
            What We Check and Why
          </h1>
          <p className="mt-4 text-muted-foreground leading-relaxed max-w-2xl">
            Our infrastructure scan runs 10 automated checks against your website. Every check is labeled
            with an evidence tier so you know exactly what is verified by real crawler behavior versus
            what is speculative.
          </p>
        </div>

        {/* Evidence tier legend */}
        <div className="mb-12 border border-border rounded-lg bg-card p-6">
          <h2 className="font-mono text-xs text-teal tracking-widest uppercase mb-4">Evidence Tiers</h2>
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-3">
              <span className="font-mono text-[10px] px-2 py-0.5 rounded border text-teal/80 border-teal/30 bg-teal/10 flex-shrink-0 mt-0.5">
                VERIFIED
              </span>
              <p className="text-sm text-muted-foreground">
                Major search engines or AI crawlers are confirmed to read and act on this signal.
                Backed by official documentation from Google, OpenAI, or protocol specifications.
              </p>
            </div>
            <div className="flex items-start gap-3">
              <span className="font-mono text-[10px] px-2 py-0.5 rounded border text-yellow-400/80 border-yellow-400/30 bg-yellow-400/10 flex-shrink-0 mt-0.5">
                EMERGING
              </span>
              <p className="text-sm text-muted-foreground">
                Proposed standard with growing adoption but not yet confirmed to be read by major AI systems.
                We include these checks when adoption exceeds 10% of top sites, labeled clearly so you can decide.
              </p>
            </div>
          </div>
        </div>

        {/* Differentiator */}
        <div className="mb-12 border border-teal/20 rounded-lg bg-teal/5 p-6">
          <h2 className="font-mono text-xs text-teal tracking-widest uppercase mb-3">
            How This Differs from HubSpot&apos;s AEO Grader
          </h2>
          <div className="grid sm:grid-cols-2 gap-6">
            <div>
              <p className="text-sm font-medium text-foreground mb-2">HubSpot AEO Grader</p>
              <p className="text-sm text-muted-foreground">
                Asks AI models &quot;what do you think of this brand?&quot; Measures sentiment, recognition,
                and share of voice. A brand perception tool. Score out of 100.
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-foreground mb-2">citability.dev</p>
              <p className="text-sm text-muted-foreground">
                Scans your actual website infrastructure. Measures whether AI crawlers can technically
                find and parse your content. A technical readiness tool. Every check shows evidence and explanation.
              </p>
            </div>
          </div>
          <p className="mt-4 text-xs text-muted-foreground border-t border-teal/10 pt-4">
            They answer &quot;does AI know you?&quot; We answer &quot;can AI find you?&quot;
            You can score 80/100 on HubSpot and still fail our scan if your site blocks AI crawlers.
            The tools are complementary.
          </p>
        </div>

        {/* Checks */}
        <h2 className="font-mono text-xs text-teal tracking-widest uppercase mb-6">
          The 10 Checks
        </h2>
        <div className="flex flex-col gap-4 mb-16">
          {checks.map((check, i) => (
            <div key={check.name} className="border border-border rounded-lg bg-card overflow-hidden">
              <div className="px-5 py-4 flex items-start justify-between gap-4 border-b border-border/60">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-muted-foreground w-6">{String(i + 1).padStart(2, "0")}</span>
                  <h3 className="text-sm font-semibold text-foreground">{check.name}</h3>
                </div>
                <span className="font-mono text-[10px] px-2 py-0.5 rounded border text-teal/80 border-teal/30 bg-teal/10 flex-shrink-0">
                  {check.tier}
                </span>
              </div>
              <div className="px-5 py-4 flex flex-col gap-3">
                <div>
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">What it checks</p>
                  <p className="text-sm text-foreground/90">{check.what}</p>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Who reads it</p>
                  <p className="text-sm text-foreground/90">{check.who}</p>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Why it matters</p>
                  <p className="text-sm text-foreground/90">{check.why}</p>
                </div>
                <a
                  href={check.source}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-mono text-xs text-teal hover:text-teal/80 transition-colors mt-1"
                >
                  Source documentation
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </div>
          ))}
        </div>

        {/* What we don't check */}
        <div className="mb-16 border border-border rounded-lg bg-card p-6">
          <h2 className="font-mono text-xs text-teal tracking-widest uppercase mb-4">
            What We Deliberately Exclude
          </h2>
          <div className="flex flex-col gap-3">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground">llms.txt</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Proposed in 2024, ~10% adoption as of 2026. No major AI company (Google, OpenAI, Anthropic, Meta)
                  confirms reading it. Only 1 of the 50 most-cited domains has one. We monitor adoption
                  but do not penalize for absence.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground">ai.txt / .well-known/llms.json</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Not established standards. No confirmed crawler support. Including them would inflate
                  your score without improving your actual AI visibility.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground">Proprietary &quot;AI scores&quot;</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  We do not generate opaque scores from black-box algorithms. Every check is a specific,
                  verifiable test with a binary PASS/FAIL result and a cited source explaining why it matters.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer CTA */}
        <div className="text-center border border-border rounded-lg bg-card p-8">
          <h2 className="text-xl font-bold text-foreground mb-2">Run the scan on your site</h2>
          <p className="text-sm text-muted-foreground mb-6">Free. No account required. Results in 10 seconds.</p>
          <a
            href="/assess"
            className="inline-block bg-teal text-primary-foreground hover:opacity-90 font-semibold font-mono text-sm px-6 py-3 rounded-md transition-opacity"
          >
            Start Free Scan
          </a>
        </div>

        {/* Open source footer */}
        <div className="mt-8 text-center">
          <a
            href="https://github.com/ChudiNnorukam/ai-visibility-readiness"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 font-mono text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <Github className="w-4 h-4" />
            Full framework on GitHub (Apache 2.0)
          </a>
        </div>
      </main>
    </div>
  )
}
