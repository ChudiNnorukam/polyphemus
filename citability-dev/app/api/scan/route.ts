import { NextRequest, NextResponse } from "next/server"

interface CheckResult {
  name: string
  slug: string
  pass: boolean
  detail: string
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
      llmsStatus,
      aiTxtStatus,
      homepage,
      httpCheck,
    ] = await Promise.all([
      headCheck(`${origin}/robots.txt`),
      headCheck(`${origin}/sitemap.xml`),
      headCheck(`${origin}/llms.txt`),
      headCheck(`${origin}/ai.txt`),
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

    // 3. llms.txt
    checks.push({
      name: "/llms.txt",
      slug: "llms",
      pass: llmsStatus === 200,
      detail: llmsStatus === 200
        ? "AI discovery file found"
        : "No /llms.txt file. AI systems use this to understand your site.",
    })

    // 4. ai.txt
    checks.push({
      name: "/ai.txt",
      slug: "ai_txt",
      pass: aiTxtStatus === 200,
      detail: aiTxtStatus === 200
        ? "AI policy file found"
        : "No /ai.txt file. This tells AI crawlers your citation preferences.",
    })

    // Parse homepage HTML for remaining checks
    const html = homepage.body || ""

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

    // 9. H1 count
    const h1Matches = html.match(/<h1[\s>]/gi)
    const h1Count = h1Matches ? h1Matches.length : 0
    checks.push({
      name: "H1 Heading",
      slug: "h1",
      pass: h1Count === 1,
      detail: h1Count === 1
        ? "Exactly 1 H1 tag found"
        : h1Count === 0
          ? "No H1 tag found on homepage"
          : `Found ${h1Count} H1 tags (should be exactly 1)`,
    })

    // 10. Open Graph
    const ogMatch = html.match(/<meta[^>]*property=["']og:title["'][^>]*/i)
    checks.push({
      name: "Open Graph Tags",
      slug: "og",
      pass: !!ogMatch,
      detail: ogMatch
        ? "Open Graph tags found for social sharing"
        : "No Open Graph tags. Links shared on social media won't show rich previews.",
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
