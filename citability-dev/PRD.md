# citability.dev - Product Requirements Document

## Objective

Complete citability.dev from "working prototype" to "revenue-ready product."
The site can accept visitors, run assessments, show results, and collect leads.

## Current State (what's built)

- [x] Landing page with Priestley frustration hook
- [x] /assess route: 15-question multi-step quiz
- [x] /results route: dynamic score + insights + tiered CTA
- [x] robots.txt, sitemap.xml, JSON-LD schema, canonical URL
- [x] GitHub repo linked ("Star on GitHub" + methodology link)
- [x] Design audit complete (74/100, 7/7 TDD PASS)

## Remaining Work (ordered by revenue impact)

### P0: Revenue-Critical (must have before first sale)

1. **Wire hero CTA to /assess**
   - "Start Free Assessment" button on landing page should navigate to /assess
   - "Free Scan" nav button should also go to /assess
   - Files: hero.tsx, nav.tsx

2. **Wire pricing CTAs to /assess**
   - "Scan Now" (free tier) -> /assess
   - "Get Report" ($147 tier) -> mailto:hello@citability.dev?subject=Quick%20Report
   - "Book Audit" ($497 tier) -> mailto:hello@citability.dev?subject=Full%20Audit
   - File: pricing.tsx

3. **Store assessment responses**
   - On submit, POST answers to an API route
   - Store in a JSON file on disk (MVP) or Vercel KV (production)
   - Include: name, email, website, all answers, score, timestamp, IP-derived location
   - File: app/api/assess/route.ts (new)

4. **Email notification on submission**
   - Send email to hello@citability.dev when someone completes the assessment
   - Include: name, email, score, tier, Q14 answer (budget signal), Q15 free text
   - Use Resend API (free tier: 100 emails/day)
   - File: app/api/assess/route.ts

### P1: Conversion Optimization

5. **Results page share button**
   - "Share your results" button that copies a shareable URL
   - File: app/results/page.tsx

6. **Assessment completion tracking**
   - Track: started, completed, abandoned (and at which question)
   - Use Vercel Analytics custom events
   - File: app/assess/page.tsx

7. **Mobile nav hamburger menu**
   - Nav links hidden on mobile, need hamburger
   - File: nav.tsx

### P2: Nice to Have (defer)

8. Email nurture sequences (requires email service setup)
9. Stripe payment integration (premature without leads)
10. Auto-run infrastructure scan from website URL input
