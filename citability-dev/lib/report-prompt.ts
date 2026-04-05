export interface ICPContext {
  name: string
  email: string
  website: string
  role: string
  frustration: string
  tried: string
  goal: string
  solution: string
  score: number
  freeText?: string
}

const ROLE_LABELS: Record<string, string> = {
  marketing: "Marketing Director / CMO",
  founder: "Founder / CEO",
  seo: "SEO Specialist",
  dev: "Developer / Technical",
  other: "Professional",
}

const FRUSTRATION_CONTEXT: Record<string, string> = {
  unknown: "doesn't know if AI mentions their brand at all",
  seo_no_ai: "invests in SEO but AI systems still ignore them",
  competitor_gap: "is being outpaced by competitors who appear in AI responses",
  no_start: "doesn't know where to begin with AI visibility",
}

const GOAL_CONTEXT: Record<string, string> = {
  benchmark: "benchmark their AI visibility vs competitors",
  first_mention: "get mentioned by ChatGPT or Perplexity for the first time",
  citations: "earn URL citations from AI systems",
  strategy: "build a complete AI visibility strategy for their team",
}

const SOLUTION_FORMAT: Record<string, string> = {
  diy: "Concise, self-executable action plan with step-by-step instructions (no jargon)",
  report: "PDF-ready report with scored sections, benchmarks, and prioritized recommendations",
  consult: "Pre-call briefing doc: 1-page summary of key findings + top 3 questions to explore on the call",
  done_for_you: "Full implementation brief with phases, effort estimates, and success metrics",
}

function scoreTier(score: number): string {
  if (score >= 8) return "HIGH: strong foundation, ready to optimize for AI citability"
  if (score >= 5) return "MEDIUM: solid base with clear gaps to fix"
  return "LOW: critical infrastructure gaps blocking AI visibility"
}

export function generateReportPrompt(ctx: ICPContext): string {
  const role = ROLE_LABELS[ctx.role] ?? "Professional"
  const frustration = FRUSTRATION_CONTEXT[ctx.frustration] ?? "wants to improve AI visibility"
  const goal = GOAL_CONTEXT[ctx.goal] ?? "improve AI visibility"
  const format = SOLUTION_FORMAT[ctx.solution] ?? "actionable report"

  const focusAreas: string[] = []

  // Frustration-driven focus
  if (ctx.frustration === "competitor_gap") {
    focusAreas.push("Lead with competitor comparison: show exactly who is beating them in AI responses and why")
  } else if (ctx.frustration === "seo_no_ai") {
    focusAreas.push("Explain the gap between traditional SEO investment and AI citability, why SEO alone doesn't transfer")
  } else if (ctx.frustration === "unknown") {
    focusAreas.push("Start with baseline measurement: give them a clear current-state picture before any recommendations")
  } else if (ctx.frustration === "no_start") {
    focusAreas.push("Prioritize a concrete first-steps roadmap over comprehensive analysis, they need direction")
  }

  // Goal-driven focus
  if (ctx.goal === "first_mention") {
    focusAreas.push("Identify the 3 specific queries where they are closest to appearing in ChatGPT / Perplexity responses")
  } else if (ctx.goal === "citations") {
    focusAreas.push("Focus on structured data, canonical signals, and source authority: these are the citation triggers")
  } else if (ctx.goal === "benchmark") {
    focusAreas.push("Include competitive benchmark against at least 2 comparable brands in their space")
  } else if (ctx.goal === "strategy") {
    focusAreas.push("Deliver a written implementation roadmap with prioritized actions, owners, and effort estimates")
  }

  // Role-driven framing
  if (ctx.role === "founder") {
    focusAreas.push("Frame everything in business outcomes: brand authority, revenue attribution, competitive moat")
  } else if (ctx.role === "marketing") {
    focusAreas.push("Provide channel-level recommendations the team can act on: content strategy, PR signals, structured data")
  } else if (ctx.role === "seo") {
    focusAreas.push("Go deep on technical signals: schema types, entity disambiguation, semantic coverage gaps, canonicalization")
  } else if (ctx.role === "dev") {
    focusAreas.push("Include implementation-ready technical details: specific schema examples, HTTP headers, JSON-LD snippets")
  }

  // Score-driven focus
  if (ctx.score < 5) {
    focusAreas.push("Address the 3 highest-leverage infrastructure gaps first: content strategy is blocked until the foundation is solid")
  } else if (ctx.score >= 8) {
    focusAreas.push("Infrastructure is solid: focus on content strategy, entity recognition, and query-level competitive analysis")
  }

  const lines = [
    `LEAD BRIEF: ${ctx.name} (${ctx.email})`,
    `Website: ${ctx.website}`,
    `Infrastructure Score: ${ctx.score}/10, ${scoreTier(ctx.score)}`,
    ``,
    `ICP PROFILE:`,
    `  Role: ${role}`,
    `  Primary frustration: ${ctx.name} ${frustration}`,
    `  90-day goal: ${goal}`,
    `  Preferred format: ${ctx.solution}`,
    ...(ctx.tried && ctx.tried !== "nothing" ? [`  Has tried: ${ctx.tried}`] : []),
    ...(ctx.freeText ? [`  Additional context: "${ctx.freeText}"`] : []),
    ``,
    `REPORT FOCUS:`,
    ...focusAreas.map((f) => `  - ${f}`),
    ``,
    `DELIVERY FORMAT: ${format}`,
  ]

  return lines.join("\n")
}

export function getIcpLabel(ctx: Pick<ICPContext, "role" | "goal">): string {
  const roleMap: Record<string, string> = {
    marketing: "CMO/Marketing",
    founder: "Founder",
    seo: "SEO Specialist",
    dev: "Developer",
    other: "Professional",
  }
  const goalMap: Record<string, string> = {
    benchmark: "Benchmarking",
    first_mention: "First Mention",
    citations: "Citation Growth",
    strategy: "Strategy Build",
  }
  return `${roleMap[ctx.role] ?? "Professional"}: ${goalMap[ctx.goal] ?? "AI Visibility"}`
}

/** Returns true if this lead's solution preference warrants a consultation call */
export function needsConsultation(solution: string): boolean {
  return solution === "consult" || solution === "done_for_you"
}
