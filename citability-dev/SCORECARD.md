# citability.dev Scorecard Architecture

Source: /scorecard-leadgen skill (Daniel Priestley framework)
Date: 2026-03-31

## ICP

Marketing directors, SEO leads, and B2B SaaS founders who invest in SEO monthly
but have never checked whether AI systems (ChatGPT, Perplexity, Claude) can find,
recommend, or cite their website.

## Landing Page Copy

### Section 1: Hook (Frustration Hook)

**Headline**: "Feeling frustrated that AI never mentions your brand, even though you invest in SEO every month?"

**Subheading**: "Answer 15 questions to find out why AI systems can't find you, and exactly what to fix."

**Alternative (Readiness Hook)**: "Are you ready to get cited by ChatGPT, Perplexity, and Claude?"

### Section 2: Value Proposition

"Take this 3-minute assessment so we can measure and improve:"

1. **Your AI Infrastructure** - Do you have the technical signals AI crawlers need?
2. **Your AI Visibility** - Does AI recognize your brand when asked about your space?
3. **Your Content Citability** - Is your content structured so AI can cite it with a link?

### Section 3: Credibility

**Who created this**: Chudi Nnorukam, AI Visibility Consultant at citability.dev

**Background**: Built the AVR (AI Visibility Readiness) framework after auditing 50+ websites
and discovering that even DA 90+ sites like Reddit and Medium fail basic AI readiness checks.

**Research backing**:
- "Answer-first structure, freshness, and structured data predict citations better than special AI files."
- "ahrefs.com (DA 92) is 100% AI-visible but only 5% AI-cited. Visibility and citations are different problems."
- "The average site scores 3/10 on our AI readiness assessment"

### Section 4: CTA

"Start Your AI Visibility Assessment"
- Takes 3 minutes to complete
- Completely free
- Get immediate, personalized recommendations

---

## 15 Questions

### Part 1: Contact Info (before quiz)
- Name (required)
- Email (required)
- Website URL (required - we need this to verify answers)
- Phone (optional)

### Part 2: Best-Practice Questions (Q1-Q10, scored 0-10)

**Q1**: "Does your website have a robots.txt file that allows search engine and AI crawlers?"
- Yes (1 pt) / No (0 pt) / I don't know (0 pt)

**Q2**: "Does your website have a sitemap.xml with up-to-date lastmod dates?"
- Yes (1 pt) / No (0 pt) / I don't know (0 pt)

**Q3**: "Does your website use structured data (JSON-LD schema) on key pages?"
- Yes, on most pages (1 pt) / On some pages (0.5 pt) / No (0 pt) / I don't know (0 pt)

**Q4**: "Do your key pages answer the main question in the first 100 words?"
- Yes, consistently (1 pt) / Sometimes (0.5 pt) / Rarely or never (0 pt)

**Q5**: "Do your blog posts or content pages use question-format headings (e.g., 'How does X work?')?"
- Yes, consistently (1 pt) / Sometimes (0.5 pt) / Rarely or never (0 pt)

**Q6**: "Does your content include original data, case studies, or measured results (not just definitions)?"
- Yes, most content has original insights (1 pt) / Some does (0.5 pt) / Mostly definitional content (0 pt)

**Q7**: "Do you monitor whether AI systems (ChatGPT, Perplexity, Claude) mention your brand?"
- Yes, I check regularly (1 pt) / I've checked once or twice (0.5 pt) / Never (0 pt)

**Q8**: "Does your content have clear 'answer-first' introductions that directly answer the page's core question?"
- Yes (1 pt) / Sometimes (0.5 pt) / No, we usually start with background/context (0 pt)

**Q9**: "Do your pages include specific numbers, methodologies, or named frameworks (not generic advice)?"
- Yes, consistently (1 pt) / Occasionally (0.5 pt) / Rarely (0 pt)

**Q10**: "Have your key pages been updated within the last 18 months?"
- Yes, most of them (1 pt) / Some of them (0.5 pt) / Rarely (0 pt) / I don't know (0 pt)

### Part 3: Big Five Qualifying Questions (Q11-Q15, not scored)

**Q11**: "Which best describes your current SEO situation?"
- We're just getting started with SEO
- We have some organic traffic but want more
- We have strong SEO and want to add AI visibility
- We're an established brand focused on maintaining market position

**Q12**: "What's the most important outcome you want to achieve in the next 90 days?"
- Get mentioned by AI for the first time
- Understand where we stand vs competitors on AI visibility
- Get actual URL citations from AI systems
- Build a complete AI visibility strategy for my team

**Q13**: "What's the biggest obstacle you've faced with AI visibility?"
- I don't know where to start
- I've optimized for SEO but AI still doesn't mention us
- I don't have the time or resources to figure this out
- I'm not sure AI visibility matters for my business yet

**Q14**: "Which approach would suit you best?"
- A free guide or checklist I can follow myself (~$0-20 intent)
- A detailed report with specific recommendations (~$50-200 intent)
- A consultation with an expert who can guide me (~$200-500 intent)
- Someone to handle the entire implementation for me (~$500-2,000 intent)

**Q15**: "Is there anything else we should know about your situation?"
- (Open text box, not required)

---

## Dynamic Results

### Scoring
- Score = sum of Q1-Q10 answers (max 10)
- Qualification tier = based on Q14 answer (budget signal)

### Results by Score

**8-10/10: "AI-Ready"**
- Headline: "Congratulations. Your AI infrastructure is strong."
- Insight 1: "You've nailed the fundamentals: [list their PASS items]"
- Insight 2: "Your gap is in [lowest-scoring area]. This is what separates visibility from citations."
- Insight 3: Based on Q12, connect their gap to their stated 90-day goal.
- Next step (High qualified / Q14 = consultation or done-for-you):
  → "Book a Strategy Call" → calendly link → maps to $497 Full Audit + Strategy Call
- Next step (Low qualified / Q14 = free guide):
  → "Download the AI Visibility Checklist" → email nurture

**5-7/10: "Foundation Ready"**
- Headline: "Well done. You've got strong foundations, but plenty of room to improve."
- Insight 1: "You're ahead of 70% of websites we audit. Here's what you're doing right: [PASS items]"
- Insight 2: "But [2-3 FAIL items] are blocking AI systems from citing you."
- Insight 3: Connect Q13 obstacle to the specific missing checks.
- Next step (High qualified):
  → "Get Your Quick Report ($147)" → shows exactly what to fix with priorities
- Next step (Low qualified):
  → "Watch: Why AI Can't Find Most Websites" → educational content

**0-4/10: "Not AI-Ready"**
- Headline: "You've got significant room to improve. Here's exactly what to focus on."
- Insight 1: "Your site is missing [N] of 10 AI readiness signals. This is common."
- Insight 2: "The good news: the highest-impact fixes (robots.txt, sitemap, schema) take under an hour."
- Insight 3: "Even ahrefs.com (DA 92) only scores 6/10. AI readiness is a new discipline."
- Next step (any qualification):
  → "Get Your Free Infrastructure Report" → runs the /citability scan operation
  → Email nurture with weekly tips

---

## Mapping to citability.dev Pricing Tiers

| Score | Q14 Answer | Offer | Price |
|-------|-----------|-------|-------|
| Any | Free guide | Free scan + email nurture | $0 |
| 5-7 | Report | Quick Report | $147 |
| 8-10 | Consultation | Full Audit + Strategy Call | $497 |
| Any | Done-for-you | Implementation Sprint | $1,997 |

---

## Implementation Plan

### Phase 1: Update Landing Page (1-2 hours)
- Replace current hero with Priestley frustration hook
- Add value proposition section (3 key areas)
- Add credibility section (bio + research stats)
- Update CTA to "Start Your AI Visibility Assessment"

### Phase 2: Build Quiz Flow (4-6 hours)
- /assess route with multi-step form
- Contact info → 10 best-practice questions → 5 qualifying questions
- Progress bar showing completion
- Store responses (Supabase or local for MVP)

### Phase 3: Dynamic Results Page (2-3 hours)
- /results route with score visualization
- 3 insights generated from answers
- Tiered CTA based on Q14 answer
- Share results functionality

### Phase 4: Email Nurture (2 hours)
- Welcome email with results summary
- 3-email sequence for each tier:
  - High: case study → strategy framework → book-a-call
  - Medium: quick win guide → deeper analysis → upgrade to report
  - Low: what is AI visibility → 5-minute fix → re-take assessment in 30 days

### Phase 5: Analytics (1 hour)
- Track: landing-to-quiz rate (target 20-40%)
- Track: quiz completion rate
- Track: results-to-CTA click rate per tier
- Track: conversion to paid per tier
