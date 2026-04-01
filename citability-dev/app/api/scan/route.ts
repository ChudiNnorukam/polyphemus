import { NextRequest, NextResponse } from "next/server"

interface CheckResult {
  name: string
  slug: string
  pass: boolean
  detail: string
}

const STOP_WORDS = new Set([
  "the", "and", "for", "with", "that", "this", "your", "from", "into", "about",
  "have", "has", "are", "was", "were", "you", "our", "their", "they", "them",
  "will", "can", "not", "but", "too", "out", "all", "any", "how", "what", "when",
  "where", "why", "who", "its", "it's", "www", "http", "https", "com",
])

function stripHtml(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, "\"")
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .trim()
}

function extractKeywords(input: string): string[] {
  return Array.from(
    new Set(
      input
        .toLowerCase()
        .match(/[a-z0-9]+/g)?.filter((word) => word.length > 3 && !STOP_WORDS.has(word)) ?? []
    )
  )
}

function evaluateAnswerFirst(html: string): { pass: boolean; detail: string } {
  const text = stripHtml(html)
  const words = text.split(/\s+/).filter(Boolean)
  const firstWords = words.slice(0, 100)

  if (firstWords.length < 35) {
    return {
      pass: false,
      detail: "Could not find enough visible body copy in the first 100 words to judge the intro.",
    }
  }

  const firstChunk = firstWords.join(" ")
  const title = html.match(/<title[^>]*>([^<]+)<\/title>/i)?.[1] ?? ""
  const h1s = Array.from(html.matchAll(/<h1[^>]*>([\s\S]*?)<\/h1>/gi))
    .map((match) => stripHtml(match[1]))
    .join(" ")
  const topicKeywords = extractKeywords(`${title} ${h1s}`)
  const overlap = topicKeywords.filter((keyword) => firstChunk.toLowerCase().includes(keyword)).length
  const hasDeclarativeSentence = /[^.!?]{35,}[.!?]/.test(firstChunk)
  const hasAnswerVerb = /\b(is|are|helps|help|provides|offer|offers|lets|turns|shows|explains|tracks|measures|improves|builds)\b/i.test(firstChunk)

  if (hasDeclarativeSentence && (overlap >= 2 || hasAnswerVerb)) {
    return {
      pass: true,
      detail: "The first 100 words appear to explain the page's core topic directly.",
    }
  }

  return {
    pass: false,
    detail: "The first 100 words do not clearly answer the page's core topic. Lead with the answer, then add context.",
  }
}

function evaluateFreshness(html: string): { pass: boolean; detail: string } {
  const candidates = [
    ...Array.from(html.matchAll(/"dateModified"\s*:\s*"([^"]+)"/gi)).map((match) => match[1]),
    ...Array.from(html.matchAll(/"datePublished"\s*:\s*"([^"]+)"/gi)).map((match) => match[1]),
    ...Array.from(html.matchAll(/<meta[^>]+(?:property|name)=["'](?:article:modified_time|og:updated_time|last-modified|dateModified|datePublished)["'][^>]+content=["']([^"']+)["']/gi)).map((match) => match[1]),
    ...Array.from(html.matchAll(/<time[^>]+datetime=["']([^"']+)["']/gi)).map((match) => match[1]),
  ]

  const validDates = candidates
    .map((value) => new Date(value))
    .filter((date) => !Number.isNaN(date.getTime()))
    .sort((a, b) => b.getTime() - a.getTime())

  if (validDates.length === 0) {
    return {
      pass: false,
      detail: "No publish or update date found in the page HTML. Expose a machine-readable modified date.",
    }
  }

  const newest = validDates[0]
  const ageInDays = Math.floor((Date.now() - newest.getTime()) / (1000 * 60 * 60 * 24))
  const recent = ageInDays <= 548

  return {
    pass: recent,
    detail: recent
      ? `Latest machine-readable update is ${newest.toISOString().slice(0, 10)} (${ageInDays} days ago).`
      : `Latest machine-readable update is ${newest.toISOString().slice(0, 10)} (${ageInDays} days ago). Target updates inside 18 months.`,
  }
}

async function checkUrl(url: string, timeout = 5000): Promise<{ status: number; body?: string }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: { "User-Agent": "citability.dev/avr-scan/1.0" },
      redirect: "follow",
    })
    const body = res.ok ? await res.text() : undefined
    return { status: res.status, body }
  } catch {
    return { status: 0 }
  } finally {
    clearTimeout(timer)
  }
}

async function headCheck(url: string, timeout = 5000): Promise<number> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const res = await fetch(url, {
      method: "HEAD",
      signal: controller.signal,
      headers: { "User-Agent": "citability.dev/avr-scan/1.0" },
      redirect: "follow",
    })
    return res.status
  } catch {
    return 0
  } finally {
    clearTimeout(timer)
  }
}

export async function POST(req: NextRequest) {
  try {
    const { url: rawUrl } = await req.json()

    if (!rawUrl || typeof rawUrl !== "string") {
      return NextResponse.json({ error: "URL is required" }, { status: 400 })
    }

    // Normalize URL
    let baseUrl = rawUrl.trim()
    if (!baseUrl.startsWith("http")) baseUrl = `https://${baseUrl}`
    // Remove trailing path, keep domain only
    const parsed = new URL(baseUrl)
    const origin = parsed.origin

    const checks: CheckResult[] = []

    // Run all checks in parallel
    const [
      robotsStatus,
      sitemapStatus,
      homepage,
      httpCheck,
    ] = await Promise.all([
      headCheck(`${origin}/robots.txt`),
      headCheck(`${origin}/sitemap.xml`),
      checkUrl(origin, 8000),
      checkUrl(`http://${parsed.hostname}`, 5000),
    ])

    // 1. robots.txt
    checks.push({
      name: "robots.txt",
      slug: "robots",
      pass: robotsStatus === 200,
      detail: robotsStatus === 200
        ? "robots.txt found and accessible"
        : robotsStatus === 0
          ? "Could not connect to check robots.txt"
          : `robots.txt returned status ${robotsStatus}`,
    })

    // 2. sitemap.xml
    checks.push({
      name: "sitemap.xml",
      slug: "sitemap",
      pass: sitemapStatus === 200,
      detail: sitemapStatus === 200
        ? "Sitemap found and accessible"
        : "No sitemap.xml found at root",
    })

    // Parse homepage HTML for content and metadata checks
    const html = homepage.body || ""
    const answerFirst = evaluateAnswerFirst(html)
    const freshness = evaluateFreshness(html)

    // 3. Answer-first content
    checks.push({
      name: "Answer-First Content",
      slug: "answer_first",
      pass: answerFirst.pass,
      detail: answerFirst.detail,
    })

    // 4. Content freshness
    checks.push({
      name: "Content Freshness",
      slug: "freshness",
      pass: freshness.pass,
      detail: freshness.detail,
    })

    // 5. JSON-LD Schema
    const jsonLdMatches = html.match(/<script[^>]*type=["']application\/ld\+json["'][^>]*>/gi)
    const hasJsonLd = jsonLdMatches && jsonLdMatches.length > 0
    checks.push({
      name: "Structured Data (JSON-LD)",
      slug: "schema",
      pass: !!hasJsonLd,
      detail: hasJsonLd
        ? `Found ${jsonLdMatches!.length} JSON-LD block(s)`
        : "No JSON-LD structured data found on homepage",
    })

    // 6. Meta description
    const metaDescMatch = html.match(/<meta[^>]*name=["']description["'][^>]*content=["']([^"']+)["']/i)
      || html.match(/<meta[^>]*content=["']([^"']+)["'][^>]*name=["']description["']/i)
    const hasMetaDesc = metaDescMatch && metaDescMatch[1].length > 10
    checks.push({
      name: "Meta Description",
      slug: "meta_desc",
      pass: !!hasMetaDesc,
      detail: hasMetaDesc
        ? `Found: "${metaDescMatch![1].slice(0, 80)}${metaDescMatch![1].length > 80 ? "..." : ""}"`
        : "No meta description found or too short",
    })

    // 7. Canonical URL
    const canonicalMatch = html.match(/<link[^>]*rel=["']canonical["'][^>]*href=["']([^"']+)["']/i)
    checks.push({
      name: "Canonical URL",
      slug: "canonical",
      pass: !!canonicalMatch,
      detail: canonicalMatch
        ? `Canonical: ${canonicalMatch[1]}`
        : "No canonical URL set. Risk of duplicate content.",
    })

    // 8. HTTPS redirect
    const httpsRedirect = httpCheck.status > 0 && homepage.status === 200
    checks.push({
      name: "HTTPS",
      slug: "https",
      pass: parsed.protocol === "https:" && homepage.status === 200,
      detail: parsed.protocol === "https:"
        ? "Site served over HTTPS"
        : "Site not using HTTPS",
    })

    // 9. Heading hierarchy
    const h1Matches = html.match(/<h1[\s>]/gi)
    const h1Count = h1Matches ? h1Matches.length : 0
    const h2Matches = html.match(/<h2[\s>]/gi)
    const h2Count = h2Matches ? h2Matches.length : 0
    checks.push({
      name: "Heading Hierarchy",
      slug: "headings",
      pass: h1Count >= 1,
      detail: h1Count >= 1
        ? `Found ${h1Count} H1 tag${h1Count === 1 ? "" : "s"} and ${h2Count} H2 tag${h2Count === 1 ? "" : "s"}.`
        : h1Count === 0
          ? "No H1 tag found on the homepage."
          : `Found ${h1Count} H1 tags.`,
    })

    // 10. Social sharing readiness
    const ogTitle = html.match(/<meta[^>]*property=["']og:title["'][^>]*content=["']([^"']+)["']/i)
    const ogDescription = html.match(/<meta[^>]*property=["']og:description["'][^>]*content=["']([^"']+)["']/i)
    const ogImage = html.match(/<meta[^>]*property=["']og:image["'][^>]*content=["']([^"']+)["']/i)
    const socialTagCount = [ogTitle, ogDescription, ogImage].filter(Boolean).length
    checks.push({
      name: "Social Sharing Readiness",
      slug: "social_sharing",
      pass: !!ogTitle && (!!ogDescription || !!ogImage),
      detail: socialTagCount >= 2
        ? `Found ${socialTagCount} Open Graph tag(s) supporting richer shared previews.`
        : "Open Graph coverage is thin. Add og:title plus og:description or og:image.",
    })

    // Calculate score
    const score = checks.filter((c) => c.pass).length
    const total = checks.length

    return NextResponse.json({
      url: origin,
      score,
      total,
      checks,
      scannedAt: new Date().toISOString(),
    })
  } catch (err) {
    console.error("Scan error:", err)
    return NextResponse.json({ error: "Scan failed. Please check the URL and try again." }, { status: 500 })
  }
}
