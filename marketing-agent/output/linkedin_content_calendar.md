# LinkedIn Content Strategy v2 - Chudi Nnorukam

## Strategy Foundation (from Analytics Audit, Mar 8 2026)

Data-driven rules based on 16 posts over 12 months (232 followers, 15,878 impressions):

1. **Every post ends with a genuine question** (fixes 0-comment problem on viral posts)
2. **Personal experience + data beats pure data** (1.37% eng vs 0.16% eng)
3. **Never put links in the post body** (link posts avg 230 impressions vs 600+ for text)
4. **Target Senior+ audience** with decision-framing, not information-sharing
5. **Post consistently 4x/week** on the same days (no gaps, no bursts)

---

## Voice: Grace Over Grind (4w5)

Every post follows these undertone rules:

- **Grace over grind**: failures are met with curiosity, not machismo
- **Self-compassion is visible**: "I sat with it" not "I pushed through"
- **Exploration over conclusions**: "I'm still thinking about this" is valid
- **Specificity over polish**: real moments (making tea, staring at the ceiling) over curated narratives
- **Warmth toward the reader**: assume they're figuring it out too

This is the differentiator. Nobody else in AI/engineering LinkedIn combines technical depth with genuine self-compassion. That's the lane.

---

## Posting Cadence: 4x/week (Tue-Wed-Thu power window + Mon)

**Post time: 10:00 AM PT (1:00 PM ET)** every post, every day. Consistent timing trains the algorithm.

| Day | Time (PT) | Content Type | Pillar | Format | Audience Target |
|-----|-----------|-------------|--------|--------|-----------------|
| Monday | 10:00 AM | Personal failure + lesson | AI Building | Text post | Senior engineers, founders |
| Tuesday | 10:00 AM | Contrarian take (decision-framed) | Philosophy | Text post | Founders, CTOs, tech leads |
| Wednesday | 10:00 AM | System/workflow reveal | Neurodivergent | Text post | Senior engineers, indie builders |
| Thursday | 10:00 AM | Technical deep-dive | Automation | Text post (link in comment) | Mid-senior engineers |

### Why this order works
- Mon: failure stories get the highest engagement rate (1.37% proven)
- Tue: contrarian takes land on the highest-engagement LinkedIn day (2026 data)
- Wed: system reveals build authority mid-week when Senior+ engineers are active
- Thu: technical content attracts ICP engineers (link to blog in first comment)
- Tue-Wed-Thu is the 2026 power window. Saturday dropped (weekend = dead zone for B2B)

---

## Content Pillars

1. **AI Building** - Claude Code, multi-agent orchestration, CLAUDE.md, evidence gates
2. **Neurodivergent** - ADHD systems, time blindness, context switching, brain-friendly workflows
3. **Automation** - n8n, workflows, trading bots, API pipelines, content distribution
4. **Philosophy** - First principles, builder mindset, contrarian takes on dev culture

---

## The Post Formula (MANDATORY for every post)

```
[HOOK: I + past tense verb + specific thing + specific number/cost]

[GRACE: How you actually felt. Not the polished version.]

[CURIOSITY: What you explored after. Not "here's the fix."]

[FRAMEWORK: 3-5 bullet points or numbered steps. The actionable insight.]

[TAKEAWAY: 1 bold sentence]

[QUESTION: Open-ended. Genuinely curious. Invites personal stories.]

---
[3-4 hashtags]
```

### Hook Rules (from data)

WORKS (600+ impressions, 1%+ engagement):
- "I [verb past tense] [specific thing]. [Specific cost/result]."
- "I deployed 4,000 lines of AI code. 45 bugs. $700+ in losses."
- "I shipped broken code three times in one week."

DOESN'T WORK (sub-300 impressions, 0% engagement):
- "Just published: [title]" (article share = algorithm penalty)
- "I just open-sourced [thing]" (announcement = no reader hook)
- "Here's my take: [generic claim]" (no personal stakes)
- Anything with an external link in the body

### Question Rules

STRONG questions (invite stories + disagreement):
- "What's the worst gap you've seen between your metrics and reality?"
- "What's a safety feature that turned out to cause more harm than good?"
- "What do you reach for first when you re-enter a complex codebase after a week away?"
- "What's a lesson you learned the expensive way?"

WEAK questions (too easy, no personal investment):
- "Agree or disagree?"
- "Drop a [emoji] if you've tried this."
- "What do you think?"
- "Which of these resonates?"

### Link Handling (NEVER in post body)

When sharing a blog post or resource:
1. Rewrite the key insight as a standalone story
2. The post must work without the link
3. Add the link as the FIRST COMMENT immediately after publishing
4. In the post, say: "I wrote the full breakdown with code examples. Dropping it in the comments."

---

## Audience Targeting Rules

Current follower split: 31.6% Entry, 25.8% Senior, 12.4% Owner, 4% Director, 4% CXO

**Goal**: Shift post VIEWERS from 43% Entry to <25% Entry within 60 days.

### How to attract Senior+ audience

Frame every post as a DECISION, not information:
- NOT: "Here are 5 AI tools for developers"
- YES: "I chose Claude Code over Cursor for my trading bot. Here's what happened after 3 months."

Reference problems only experienced devs face:
- Architecture tradeoffs at scale
- Managing tech debt while shipping
- Choosing build-vs-buy when you're the technical founder
- Running AI agents in production (not tutorials, war stories)

### Engagement targeting (comment on 5 Senior+ profiles/day)

Before posting each day, spend 10 minutes:
1. Find 5 posts from Senior Engineers, CTOs, or Founders in AI/DevTools
2. Leave a thoughtful comment (not "Great post!")
3. Reference something specific from their post
4. This seeds the algorithm to show YOUR posts to their network

---

## 8-Week Content Sprint (Mar-Apr 2026)

### Week 1

**Mon**: "I deployed 4,000 lines of AI-generated code into a live trading system. 45 bugs. $700+ in real losses.

And honestly? I didn't spiral. I made coffee, opened a blank doc, and got curious.

Not 'what went wrong' in the dramatic sense. More like: what did I trust that I shouldn't have? Where did I stop questioning?

What I found:

1. Sentinel values masquerading as real data (the API returned 0.0 on failure and I never checked)
2. Race conditions between async fills and state checks
3. In-memory dedup sets that vanished on every restart
4. A profit formula that could never go negative (so my dashboard showed 100% win rate while I was bleeding money)

The fix wasn't better prompts or longer code reviews. It was building evidence gates: automated checks that block deployment until the code PROVES it works. Not 'should work.' Proves.

I'm still finding bugs from that deployment, by the way. Grace with yourself is part of the process.

What's the most expensive bug you've shipped to production, and how long before you noticed something felt off?

---
#AI #ClaudeCode #SoftwareEngineering #BuildInPublic"

**Tue**: "I watched a senior engineer paste a bug report into ChatGPT and hope for the best.

I watched another senior engineer break the problem into 5 small, verifiable chunks, run each through Claude Code, and catch 3 AI mistakes before production.

Same tool. Completely different relationship with it.

The difference wasn't technical skill. It was something I've been thinking about a lot lately: executive function. The ability to decompose problems, build verification steps, and treat AI output as a first draft instead of gospel.

Here's what makes me smile about this: ADHD brains have been building compensation systems for unreliable executive function our entire lives. We've had decades of practice at this.

The devs struggling with AI tools right now aren't lacking skill. They're trying to white-knuckle their executive function into being perfect. It can't be. And that's okay. Build systems that don't need it to be.

What's a constraint you've had to build into your workflow because willpower alone wasn't cutting it?

---
#AI #SoftwareEngineering #BuildInPublic #FirstPrinciples"

**Wed**: "I shipped 3x more features than my neurotypical teammates last year.

Not because I 'overcame' ADHD. Not because I worked harder. Because I finally stopped pretending my brain works like theirs and built systems for the brain I actually have.

Three things I stopped fighting:

1. Time blindness: 45-minute task atoms. When the timer ends, I save state to a file, not to my brain. One commit per atom. No exceptions, even when I'm in flow.
2. Working memory gaps: CLAUDE.md holds context I physically cannot retain across sessions. It's not a crutch. It's architecture.
3. Completion blindness: No task counts as 'done' until the build passes. I banned the phrase 'should work' from my vocabulary because my brain will absolutely lie to me about what's finished.

The thing that took me years to learn: neurotypical productivity advice applied to an ADHD brain isn't just unhelpful. It's self-sabotage at scale. And giving yourself permission to stop following it is one of the kindest things you can do.

What's one system you've built that would completely fall apart if you relied on willpower alone?

---
#ADHD #SoftwareEngineering #Neurodivergent #Productivity"

**Thu**: "One file replaced 3 productivity apps for me.

It's called CLAUDE.md. It sits in my project root and tells my AI coding agent everything it needs to know:
- What the project is and how it's structured
- Rules it must follow (never skip tests, never claim 'should work')
- Patterns to use (Svelte 5 runes only, no legacy syntax)
- Quality gates (build must pass, screenshots required)

I went from mass-producing bugs to mass-producing working features. And the shift was honestly kind of beautiful to watch.

The biggest realization: I'd spent months optimizing my prompts. Tweaking wording. Adjusting temperature. It was like polishing the steering wheel instead of looking at the road. The real lever was system instructions.

I wrote the full approach with code examples. Dropping it in the comments.

When you context-switch away from something complex, what's your way back in? I'm genuinely curious because I'm always looking for better re-entry patterns.

---
#ClaudeCode #AI #DeveloperProductivity #SoftwareEngineering"

### Week 2

**Mon**: "My database said I had $1,052 in profits from my trading bot.

My wallet had $213.

I stared at both numbers for a long time. Made tea. Stared some more.

The gap? A dry-run P&L formula that could never produce a negative number. Every simulated trade was a 'win.' My dashboard showed 100% win rate. I went live based on that number. Why wouldn't I? The data said it was working.

6 trades. 0 wins. Real money gone.

I'm not going to pretend I handled it gracefully in the moment. But what I did next, I'm proud of. Instead of scrambling to fix it, I sat down and asked: what other metrics in my system are structurally incapable of telling me bad news?

Three checks I now run before trusting any automated metric:
1. Can this formula produce a negative result? If not, it's lying.
2. Who or what makes decisions based on this number?
3. What happens downstream if this number is wrong?

What's a metric that told you everything was fine when it very much wasn't?

---
#AI #SoftwareEngineering #BuildInPublic #TradingBot"

**Tue**: "I spent 2 months crafting the 'perfect' Claude prompt.

Tweaked temperature. Added few-shot examples. A/B tested phrasings. Read every blog post about prompt engineering. Tracked improvement at every step.

60 hours of work. 10% improvement.

Then I spent 2 days building a CLAUDE.md file with project rules, evidence gates, and quality checks. No prompt tweaking. No temperature games. Just: 'Before you commit, these gates must pass.'

5x improvement.

I was so deep in the weeds that I couldn't see the actual lever. It's a pattern I keep noticing in myself: I'll optimize the thing I can SEE (the prompt text) and ignore the thing that actually matters (the constraints around the output).

This is why 99% of prompt engineering advice feels productive but isn't. You're polishing the input when you should be constraining the output. And honestly, once I saw it, I couldn't unsee it.

What's a problem where you spent weeks optimizing the wrong variable before finding the real lever? I find these stories weirdly comforting.

---
#AI #SystemsThinking #FirstPrinciples #SoftwareEngineering"

**Wed**: "I lost 6 days last month to context switching.

Not 6 days of lost time. 6 full days of RECOVERY. Each interruption cost me about 40 minutes to fully re-engage. Research says 23 minutes for neurotypical brains. ADHD adds overhead that nobody talks about.

Here's a specific moment: I was deep in an auth system. Laptop closes. Next morning, I open my project and brace for the 40-minute rebuild. The dread of starting over is sometimes worse than the actual work.

Instead, I open CLAUDE.md. It holds the entire project state, current focus, what I was debugging, next steps.

Claude reads it. Picks up exactly where I left off.

No mental rebuild. No 40-minute tax. The AI carries the context my working memory drops. And I'm not going to lie, the first time it worked, I got a little emotional. Years of losing context every single day, and suddenly that problem just... wasn't there anymore.

That one file is probably worth $50K/year in reclaimed productive hours. But the real value is the relief. The quiet relief of not losing things anymore.

What do you reach for first when you need to re-enter a complex codebase you've been away from? I'm always collecting new patterns for this.

---
#ADHD #Productivity #SoftwareEngineering #ClaudeCode"

**Thu**: "I use 50 AI agents. Most of them cost nearly nothing.

The secret isn't having 50 agents. It's realizing that 70% of subtasks don't need the expensive model.

My routing system:
- Haiku (cheapest): file search, test running, docs, status checks
- Sonnet (mid): code implementation, refactoring, reviews
- Opus (expensive): architecture decisions, money decisions, deep debugging

This cut my AI costs by roughly 60% without losing quality on the tasks that matter.

The insight that changed everything: match model intelligence to task difficulty. Using Opus for a file search is like hiring a principal engineer to sort Jira tickets. It'll work, but you're burning money and attention on the wrong thing.

I spent a while just playing with this, trying different combinations, seeing what each model could handle. That exploration phase was genuinely fun. Not optimizing yet, just... learning what each one is good at.

Full breakdown with the routing table in the comments.

What's a task you assumed needed the smartest model but turned out to work perfectly with something simpler?

---
#AI #ClaudeCode #DeveloperProductivity #Automation"

### Week 3

**Mon**: "I shipped a trading bot that mass-produced losses for 3 weeks before I caught it.

The bot was doing everything 'right':
- Entering positions based on momentum signals
- Setting stop losses and profit targets
- Logging every trade to a database

Everything looked correct in isolation. That's what made it so hard to see.

The problem: the exit logic was incompatible with the entry logic. It bought cheap binary tokens at $0.05-$0.13. Then the stop loss fired at -15%, selling at $0.04. The profit target fired at +20%, selling at $0.06. Meanwhile, the correct play was to hold to resolution at $1.00.

Every safety mechanism was actively destroying the expected value. The guardrails were the bug.

I sat with this one for a full day before I could even look at the code again. Not because it was technically hard, but because I felt foolish. Three weeks. I had to give myself permission to just be someone who missed something for three weeks.

The lesson: when you add a new entry strategy, audit every exit path for compatibility. Your stop loss from strategy A might be toxic for strategy B.

What's a 'safety feature' in your system that turned out to cause more harm than good? How long before you noticed?

---
#AI #TradingBot #SoftwareEngineering #BuildInPublic"

**Tue**: "A hiring manager asked to see my code samples.

I sent her my CLAUDE.md file instead. It felt like a weird thing to do. I almost chickened out and sent a GitHub link.

She read it and said: 'I learned more about how you think in 5 minutes than I would have in a 2-hour code review.'

What did she see?

- My rules: 'No task counts as done until the build passes' (quality gates)
- My task atoms: 45 minutes (scope and sustainability)
- My agent routing: architect/executor/verifier/debugger (systems thinking)
- My escalation paths: what needs Opus vs Sonnet vs Haiku (tradeoff awareness)
- My failure modes: 'Evidence gates prevent false confidence' (learning from mistakes)

A resume tells you what someone claims. A CLAUDE.md shows how they actually work. The messy, real, iterative way they actually think.

I have this hunch that in 2 years, teams will evaluate candidates by reviewing their system instructions, not their cover letters. The people who externalize their thinking well will have a huge advantage.

What would someone learn about how you work by reading your project config files? I think most people would be surprised by what's in there.

---
#AI #SystemsThinking #FirstPrinciples #SoftwareEngineering"

**Wed**: "I automated myself out of the time-blindness problem.

Not by trying harder to feel time correctly. That's impossible with ADHD, and I wasted years being frustrated about it before I gave myself permission to just... stop trying.

Instead, I built systems that don't need me to perceive time at all:

- Every task is a 45-minute atom. Save state to file at the end, not to brain.
- No task is marked 'done' until the output passes an automated check (build, test, artifact).
- Time tracking is automatic. Git commits timestamp. CI logs track duration. I never estimate.

Result: I don't need to judge when I'm 'finished.' The system tells me. And there's something deeply peaceful about that.

The second-order benefit I didn't expect: this works for ANYONE. Not just ADHD brains. The system makes time estimation irrelevant for the whole team.

I can hand off work to AI agents because they don't lose time context. They lose MEMORY. But memory, I fixed with CLAUDE.md. Time, I fixed with atoms and automated gates. Two problems, two systems, zero willpower required.

What's an unreliable human capability you've successfully replaced with a system? I love hearing these stories.

---
#ADHD #Automation #SystemsThinking #SoftwareEngineering"

**Thu**: "I cut my Claude API costs by 60% with one pattern: progressive disclosure.

Old way: dump the entire codebase into every prompt. 200K tokens. Expensive. And honestly, kind of lazy on my part.

New way: load metadata first (~400 tokens). Only load full files when the agent actually needs them.

Like a table of contents vs reading every chapter before answering a question. Once I saw it that way, I couldn't believe I'd been doing it the other way.

Three tiers:
- Tier 1: skill names and triggers (~400 tokens)
- Tier 2: input/output schemas (~800 tokens, loaded on first use)
- Tier 3: full implementation (~2000 tokens, loaded only on explicit request)

Same quality. 60% fewer tokens. The model doesn't need everything upfront. It needs the right thing at the right time.

I spent a weekend just experimenting with different tier boundaries, which was genuinely fun. That exploration phase before optimizing is my favorite part of any problem.

Detailed implementation with code in the comments.

What information do you load into every prompt that might not actually be needed until later? I bet there's more than you think.

---
#AI #ClaudeCode #Automation #DeveloperProductivity"

### Week 4

**Mon**: "I built a blog, a trading bot, and a design system in 4 weeks.

I have ADHD. I cannot hold a complex plan in working memory. Most days I can barely hold what I had for breakfast. And normally, that kills projects this ambitious.

So I stopped trying. I delegated memory to AI agents with specific swim lanes:

1. Architect: plans the work, never writes code
2. Executor: implements, never deviates from the plan
3. Verifier: audits quality, never edits code
4. Debugger: fixes failures, never invents new approaches

Each agent has guardrails that prevent scope creep. Like a team where everyone stays in their lane not because they're limited, but because focus is a gift.

The project doesn't live in my head. It lives in the system architecture. I just manage the handoffs. And honestly? That's a really peaceful way to build things.

The ADHD insight that took me years: I wasn't failing as a planner. I was failing at HOLDING the plan. Once I built a system that doesn't need me to hold anything, scale became possible. And I wish someone had told me sooner that it was okay to stop trying to hold it all.

How do you structure your work when you know you can't hold the entire complexity in your head? I'm always curious how other people solve this.

---
#ClaudeCode #AI #ADHD #SystemsThinking"

**Tue**: "Every optimization is a bet. Most developers don't do the math.

I spent 2 months optimizing my Claude prompts. 60 hours. 10% improvement.

Then I spent 2 days building evidence gates. 5x improvement.

The prompt work had better odds of WORKING (high probability, low payoff). The system change had better odds of MATTERING (lower probability, massive payoff).

I keep falling into this trap: I default to the safe, visible optimization because it feels productive. The high-leverage bet feels uncomfortable because if it fails, I 'wasted' the time. My brain wants the guaranteed small win over the uncertain big one.

But the math never lies. 60 hours at 10% vs 16 hours at 500%.

Three bets I've made this year, since we're being honest:
1. Multi-agent orchestration over monolithic AI assistant (paid off: 3x throughput)
2. Trading bot over manual prediction markets (lost: $700 learning cost, still processing this one)
3. Personal blog over social-only content (jury's still out, and I'm okay with that)

What's a technical bet you made that felt risky at the time? Whether it paid off or not, I'd love to hear it.

---
#FirstPrinciples #SoftwareEngineering #BuildInPublic #SystemsThinking"

**Wed**: "Hyperfocus is not a superpower. It's technical debt you pay in crashes.

I used to wear it like a badge. 14-hour coding sessions. Shipping entire features in one sitting. Feeling invincible.

Then crashing for 2 days. Unable to code. Unable to think. Teammates waiting on me while I stared at the ceiling feeling like a fraud.

That's not productivity. That's a boom-bust cycle. And it took me a long time to stop romanticizing it.

The fix wasn't willpower or discipline. It was compassion. Rules that protect me from myself:

1. Session cap: 5 sessions per day, 45 minutes each. Full stop, even during flow. ESPECIALLY during flow.
2. Evidence gates: Stop when the build passes, not when I 'feel done.'
3. Ship small: One feature per session, not the entire vision.
4. Handoff state: Write session notes so tomorrow-me can pick up gently.

Result: I went from 3 dramatic days per week to 5 quiet days per week. Less exciting. Infinitely more sustainable. And my teammates can actually count on me now, which matters more than any heroic coding session ever did.

How do you handle the productivity cycles that come with how your brain is wired? No judgment, whatever the answer is.

---
#ADHD #SoftwareEngineering #Sustainability #BuildInPublic"

**Thu**: "I automated my entire content pipeline. Blog to LinkedIn to Dev.to to newsletter in one command.

Stack:
- n8n workflow triggers on new blog post
- Claude API reformats for each platform's style
- LinkedIn API publishes the text post
- Dev.to API cross-posts with canonical URL back to chudi.dev

Time savings: 3 hours per post reduced to 5 minutes of review.

But here's the part nobody talks about: the automation forced me to write better source content. When one piece feeds 4 channels, you can't be lazy with the original. The constraint made the work better. I love when that happens.

Building this was one of the most fun weekends I've had in a while. Just exploring how the pieces fit together, no pressure, no deadline. That playful exploration phase is where my best work happens.

Full architecture and code walkthrough in the comments.

What's a workflow you've automated where the automation itself made you better at the original task? Those feedback loops are fascinating to me.

---
#Automation #n8n #AI #DeveloperProductivity"

### Week 5

**Mon**: "I chose Claude Code over Cursor 3 months ago. Here's what actually happened.

Month 1: Slower. The CLI felt primitive. I missed autocomplete. I missed the IDE integration. I almost switched back three times. Honestly, I was grumpy about it.

Month 2: I built a CLAUDE.md file and a multi-agent system. Suddenly the CLI wasn't a limitation. It was a feature. I could orchestrate 5 agents in parallel. Cursor can't do that. The moment it clicked, I laughed out loud.

Month 3: I shipped a blog, a trading bot, and a design system. All from the terminal. My output is higher than it's ever been, and the process feels more like play than work.

The lesson I keep relearning: Cursor optimizes for the NEXT LINE of code. Claude Code optimizes for the NEXT SYSTEM. They're different tools for different modes of thinking.

If you're writing features, Cursor wins. If you're building architecture, Claude Code wins. Choose based on what you're actually doing, not what the Twitter hype says. And give yourself permission to feel slow while you're learning. That's just what growth feels like.

What's a tool you almost gave up on that became essential once you changed how you used it?

---
#ClaudeCode #AI #SoftwareEngineering #DeveloperProductivity"

**Tue**: "I automated myself out of a job title.

2 years ago: 'Full-Stack Developer.' I wrote code, reviewed code, deployed code, monitored code.

Today: I write system instructions. AI agents write, review, deploy, and monitor the code. I manage the architecture and make decisions.

My job title should probably be 'AI Systems Architect.' But that doesn't exist on most org charts yet. I'm in this weird in-between space and I'm trying to be okay with not having a clean label for what I do.

Here's what I actually think about this, without the LinkedIn polish: the developers who succeed in the next 5 years won't be the best coders. They'll be the best system designers. The ones who can decompose problems, set quality constraints, and orchestrate agents.

Coding skill becomes a commodity. Architectural thinking becomes the premium.

I'm not saying this to be provocative. I'm saying it because it already happened to me, and I'm still figuring out what it means. Some days that's exciting. Some days it's unsettling. Both are true.

What part of your current role do you think will look completely different in 2 years?

---
#AI #SoftwareEngineering #FirstPrinciples #BuildInPublic"

**Wed**: "The accommodations I built for my ADHD became the tools my entire team uses.

I built session state files because I can't hold context overnight. Now the whole team uses them for async handoffs.

I built evidence gates because I can't trust my 'gut feel' on completion. Now nobody ships without passing them.

I built 45-minute task atoms because I lose track of time. Now the team uses them for sprint planning.

I didn't set out to build accessibility features. I built reliability engineering. My brain just needed it first.

There's something really beautiful about this pattern: constraints born from disability often become best practices for everyone. Curb cuts were designed for wheelchairs. Everyone uses them. Closed captions were designed for deaf viewers. Everyone uses them in noisy airports.

My ADHD systems weren't accommodations. They were better engineering that nobody else had been forced to discover yet.

What's a personal workaround you built for yourself that turned out to be useful for people who didn't share your original problem?

---
#ADHD #Neurodivergent #SoftwareEngineering #Accessibility"

**Thu**: "My AI agent failed silently for 2 weeks. Here's how I finally caught it.

The agent was supposed to monitor my trading bot's health. Check signals, verify fills, alert on anomalies.

It ran every 15 minutes. Green status. No alerts. I slept peacefully for 12 days.

Except the monitoring target had been down the whole time. The agent was checking a stale cache file, not the live system. Green status was a comfortable lie.

When I found out, I didn't panic. I made tea. Then I got curious: what's the meta-problem here? The answer hit me: I had no observability on the observer.

Three rules I now follow for any automated monitoring:
1. The monitor must verify its own data source is fresh (staleness check)
2. Absence of alerts is not proof of health. Require periodic positive confirmation.
3. Every monitor needs a 'watchdog' that verifies the monitor itself ran recently.

Quis custodiet ipsos custodes. Who watches the watchmen. The answer is: another agent, with simpler logic and harder-to-fool checks.

Full setup with code in the comments.

What's the sneakiest silent failure you've dealt with? The ones that look healthy are always the scariest.

---
#Automation #Observability #SoftwareEngineering #AI"

### Week 6

**Mon**: "I mass-produced 3 weeks of content in one afternoon. Then I read it back and felt nothing.

The AI wrote 16 LinkedIn posts. Technically correct. Properly formatted. On-brand.

But they all sounded the same. Generic hooks. Safe takes. Zero personality. Zero me.

The problem was obvious once I saw it: I gave the AI my TOPICS but not my STORIES. It can generate frameworks all day. It can't generate the time I lost $700 to a bug I was sure I'd fixed, or the quiet shame of crashing after a 14-hour hyperfocus session, or the relief of a system finally working after three failed attempts.

The fix: I write the STORY (the failure, the feeling, the specific moment). The AI handles the STRUCTURE (formatting, frameworks, CTAs).

AI content without human stories is noise. Human stories without AI structure is a diary. The combination, when it works, is something I'm genuinely proud of.

What's a task you delegated to AI that came back technically perfect but missing something essential?

---
#AI #ContentCreation #BuildInPublic #SoftwareEngineering"

**Tue**: "I changed my mind about what 'done' means. It cost me $1,300 to learn.

Old definition: 'It works on my machine. Tests pass. Ship it.'

Three deployments later: $700 from a metric that couldn't go negative, $400 from exit logic incompatible with entry logic, $200 from dedup sets that vanished on restart.

All three 'worked on my machine.' All three passed tests. All three cost real money.

I could beat myself up about this. I used to. Now I just see it as tuition for a lesson I needed.

New definition of done:

1. Build passes (table stakes)
2. Types clean (no suppressions)
3. Tests pass (including edge cases I tried to skip)
4. Evidence gates: can the success metric fail? What consumes this output downstream? What's the financial consequence of a wrong answer?

'Done' is not a feeling. It's a checklist with teeth. And building that checklist was an act of self-compassion, not punishment. I'm protecting future-me from present-me's optimism.

How has your definition of 'done' evolved over your career? I think the trajectory says a lot about how we grow.

---
#SoftwareEngineering #FirstPrinciples #BuildInPublic #AI"

**Wed**: "I stopped taking my ADHD medication to debug a critical production issue. Never again.

The logic seemed sound: I need to hyperfocus. Meds cap my focus. Skip the meds, unlock the hyperfocus.

What actually happened: 6 hours of scattered debugging. Missed an obvious race condition because I couldn't hold two async threads in working memory simultaneously. Deployed a 'fix' that introduced a new bug. Spent the next day cleaning up both.

With meds the next day: found the root cause in 40 minutes. Fixed it properly. Wrote a regression test.

Hyperfocus without working memory is just tunnel vision. You go deep on the wrong thing and can't course-correct.

I share this because I think a lot of neurodivergent people in tech quietly make these tradeoffs. And nobody talks about how badly they backfire. It's not weakness to need your meds. It's not weakness to need accommodation. It's architecture.

I now have a hard rule: never trade health for productivity. The math never works out. And you deserve to treat yourself better than that.

What's a shortcut you took for productivity that backfired? No judgment here, genuinely.

---
#ADHD #Neurodivergent #SoftwareEngineering #Productivity"

**Thu**: "I handed a $10K/month manual workflow to a bot. Here's the catch nobody talks about.

The workflow: monitoring prediction markets, evaluating signals, placing trades, managing exits. Took me 4-5 hours daily.

The bot now handles it in milliseconds.

But here's what surprised me:

1. The bot found edge cases I'd never encountered manually (market resolution during off-hours)
2. The bot had NO judgment for novel situations (it kept trading during an exchange outage)
3. Debugging the bot took longer than doing the work manually for the first 2 months
4. The bot exposed that my 'intuition' was wrong 40% of the time (that one stung)

Net result: the bot is better than me at execution. I'm better than the bot at strategy. The combination works. Neither alone does.

Automation doesn't replace judgment. It reveals how little judgment you were actually using. And sitting with that realization is humbling in a way I think is healthy.

What's a task you automated where the automation revealed something uncomfortable about your manual process?

---
#Automation #TradingBot #AI #BuildInPublic"

### Week 7

**Mon**: "I redesigned my entire website in 2 weeks. Without opening Figma. Without having 'design taste.'

I opened a terminal instead.

Step 1: A Chrome MCP agent visited 3 reference sites and extracted their design tokens (colors, spacing, typography, layout patterns).
Step 2: I codified those tokens into a system.md file. 26 CSS custom properties. One source of truth.
Step 3: AI agents built every component against the system. Not 'make it look good.' Instead: 'Use --color-primary for buttons, 24px radius for cards, 1.75 line-height for body text.'

Result: every page looks intentional. Not because I suddenly developed design taste, but because I have design constraints. And constraints, it turns out, are more valuable than taste.

I found this process genuinely delightful. Extracting design tokens from sites I admire, playing with combinations, seeing a system emerge. Engineering applied to aesthetics. Two worlds I love, together.

What's a non-engineering domain where you've applied engineering constraints and gotten better results than you expected?

---
#AI #WebDev #DesignSystems #SoftwareEngineering"

**Tue**: "The best engineers I know make fewer decisions per day, not more.

They build systems that eliminate decisions:
- Linters decide code style (no debates in PRs)
- Evidence gates decide 'done' (no gut-feel shipping)
- Model routing decides which AI to use (no manual selection)
- Task atoms decide scope (no 'how big should this be?' paralysis)

Every decision you automate away is cognitive load you reclaim for the decisions that actually matter: architecture, strategy, tradeoffs.

I used to pride myself on making fast decisions. Now I pride myself on making fewer decisions. The ones I still make are higher quality because I'm not decision-fatigued from choosing tabs vs spaces at 3pm.

This is especially true for ADHD brains. Every decision, no matter how small, drains executive function from the same limited pool. Automate the trivial ones. Protect your capacity for the important ones. It's not laziness. It's resource management. It's kindness toward your future self.

What's a decision you've successfully automated out of your daily workflow? The smaller and dumber the decision, the more I want to hear about it.

---
#FirstPrinciples #SystemsThinking #SoftwareEngineering #Productivity"

**Wed**: "ADHD developers have a hidden advantage in AI-era development. And it's not hyperfocus.

The core struggle of ADHD: you can't hold things in working memory. Plans dissolve. Context evaporates. Multi-step processes collapse.

The core requirement of AI-assisted development: externalize your plans, context, and processes into files that agents can read.

See it?

We've been doing this our ENTIRE LIVES. Building external systems to compensate for unreliable internal systems. That's not a disability in the age of AI agents. That's a head start.

Neurotypical developers are learning for the first time what we already know: you cannot rely on your brain to hold everything. You must build systems that hold it for you.

CLAUDE.md. Session state files. Evidence gates. Task atoms. These aren't ADHD accommodations anymore. They're best practices for working with AI.

I find this genuinely moving. The thing I was most ashamed of for years, my inability to hold context, turned out to be the exact skill the industry needs right now. Life is weird like that.

What's a skill you developed to compensate for something you struggled with that unexpectedly became your biggest professional advantage?

---
#ADHD #Neurodivergent #AI #SoftwareEngineering"

**Thu**: "I ran 10,000 AI generations in one week without blowing my API bill. Here's the architecture.

The naive approach: send every request to the best model. Opus for everything. $800/week projected. My wallet said no.

My approach: a 3-tier routing system with aggressive caching. I spent a rainy Saturday building it and honestly had more fun than I expected.

Tier 1 - Prompt caching (90% savings on repeated context):
Cache the system prompt and project rules. Only the user message changes per call.

Tier 2 - Model routing (60% savings on task allocation):
Haiku for exploration and docs. Sonnet for implementation. Opus only for architecture decisions.

Tier 3 - Batch API (50% savings on non-urgent tasks):
Daily reports, SEO audits, and content generation go through the batch endpoint. Results in 24 hours at half price.

Combined: roughly 95% cheaper than naive Opus-for-everything. Actual weekly cost: under $40 for 10K generations.

Full implementation guide with code in the comments.

What's your most effective cost optimization for AI API usage? I'm always learning new tricks here.

---
#AI #ClaudeCode #Automation #DeveloperProductivity"

### Week 8

**Mon**: "I mass-produced 10 blog posts in a week. 8 of them were garbage.

The 2 that worked? Both started with a real failure I was embarrassed to share.

The 8 that failed? All started with a framework I thought was clever.

The pattern is obvious now:
- Posts that start with 'I failed at X' get read, shared, and commented on.
- Posts that start with 'Here's a framework for X' get scrolled past.

People don't connect with frameworks. They connect with the struggle. The framework is the reward for reading the struggle. Not the other way around.

I now write every post with a simple test: 'Would I be slightly uncomfortable publishing this?' If yes, it's probably good. If no, it's probably generic.

This was hard to learn because my 4w5 instinct is to polish everything until it feels safe. But safety is the enemy of connection. Vulnerability, the real kind where you actually don't know how people will react, that's where the good stuff lives.

What's something you've shared publicly that you were nervous about? How did people respond?

---
#ContentCreation #BuildInPublic #SoftwareEngineering #FirstPrinciples"

**Tue**: "I mass-produce software because I'm terrified of forgetting.

Every system I build, every evidence gate, every session state file, every memory layer for my AI agents. They all exist because my brain drops things.

ADHD didn't make me a worse engineer. It made me a more DEFENSIVE engineer. I trust nothing to memory. I verify everything with evidence. I build redundancy into every process.

Neurotypical engineers can afford to be casual about state management. I can't. And I've made peace with that. More than peace, actually. I've come to see it as a quiet kind of strength.

The builders who will thrive in the AI era aren't the most talented. They're the most systematic. The ones who build reliability into their process because they know, with full self-compassion, that they can't rely on themselves for everything.

That's not a weakness. That's an architecture pattern. And honestly? It's one of the things I like most about how I work.

What's a personal limitation that forced you to build something better than what you would have built without the constraint?

---
#ADHD #Neurodivergent #SoftwareEngineering #FirstPrinciples"

**Wed**: "I built a memory system for my AI agents because my own memory is unreliable.

With ADHD, I lose context between sessions. I forget decisions I made yesterday. I re-solve problems I already solved last week. It used to frustrate me endlessly.

So instead of fighting it, I got curious about it. What if I built the memory system I wish my brain had?

- Event log: every decision, file edit, and blocker gets timestamped and appended
- Priority notepad: critical state that must survive context loss
- Project memory: architectural decisions and their rationale

When I start a new session, the AI reads the memory files and picks up where I left off. It remembers what I can't.

The unexpected benefit: I've started reading my OWN memory files. Reviewing past decisions, seeing patterns in my work, catching recurring mistakes. It's like having a journal that's actually useful.

The AI's memory became my external brain. Not just for the AI. For me. And there's something really tender about that. Building a tool to help a machine, and discovering it helps the human more.

What's a system you built for one purpose that turned out to be more valuable for a completely different reason?

---
#ADHD #AI #SystemsThinking #Neurodivergent"

**Thu**: "n8n replaced my entire marketing stack. Here's the workflow.

Before: Manually publish blog post. Copy-paste to LinkedIn. Reformat for Dev.to. Schedule newsletter. Update sitemap. Submit to Google Search Console. 3+ hours per post. I dreaded it.

After: Publish blog post. n8n triggers automatically. I go make tea.

The workflow:
1. Webhook fires on new git commit to content/ directory
2. Claude API reformats the post for LinkedIn style (text-only, no links in body)
3. LinkedIn API publishes. Link goes as first comment via a delayed second API call.
4. Dev.to API cross-posts with canonical URL.
5. IndexNow pings Bing and Yandex for instant indexing.
6. Google Search Console gets a sitemap re-submission.

Total human effort: 5 minutes of review before the workflow fires.

The n8n workflow is 47 nodes. Took 3 days to build. Saves 3 hours per post. Break-even after 8 posts. But the real value isn't the time saved. It's removing the dread. When publishing is painless, you publish more. And publishing more is how you get better.

Full workflow JSON export in the comments.

What's the most complex automation you've built, and was the joy of building it part of the value?

---
#Automation #n8n #DeveloperProductivity #AI"

---

## Engagement Protocol

### Pre-posting routine (10 min/day, BEFORE publishing)

1. Open LinkedIn. Find 5 posts from Senior Engineers, CTOs, or Founders in your niche.
2. Leave a substantive comment on each (reference something specific).
3. Follow anyone who has engaged with your recent posts.
4. THEN publish your post.

This primes the algorithm to show your content to the right network tier.

### Post-publishing routine (within 30 min)

1. If the post references a link: drop it as the first comment immediately.
2. Reply to every comment within 4 hours.
3. First reply: end with a question (shows genuine interest).
4. Second reply: acknowledge their point, close warmly (no question).
5. Max 2-3 exchanges per thread.
6. Follow anyone who comments.

### Comment quality standards

Your comments on OTHER people's posts must:
- Reference something specific from their post (not "Great insight!")
- Add your experience or a contrarian angle
- Be 2-4 sentences (not a one-liner, not an essay)
- Never pitch yourself or link to your content

---

## Newsletter: PAUSED

"The AI Builder's Edge" newsletter has 1 subscriber and 0 views.

**Do not invest time here until:**
- 500+ followers (currently 232)
- Consistent 2%+ engagement rate on posts
- People are actively asking for deeper content in comments

When reactivated: repurpose highest-performing posts into deeper newsletter editions.

---

## Hashtag Strategy

| Pillar | Hashtags |
|--------|----------|
| AI Building | #AI #ClaudeCode #SoftwareEngineering |
| Neurodivergent | #ADHD #Neurodivergent #Productivity |
| Automation | #Automation #n8n #DeveloperProductivity |
| Philosophy | #BuildInPublic #FirstPrinciples #SoftwareEngineering |

Always include #SoftwareEngineering or #AI (largest relevant audiences).
Never use more than 4. Never use em-dashes anywhere in posts.

---

## Metrics to Track (Updated Targets)

| Metric | Month 1 Target | Month 2 Target | Why |
|--------|---------------|----------------|-----|
| Avg engagement rate | **2%** | **4%** | Currently 0.16-1.37%. #1 priority. |
| Avg comments/post | **2** | **5** | Currently 0. Comments drive algorithm reach. |
| Followers | +100 | +300 | Slower growth, higher quality (Senior+). |
| Entry-level viewer % | <35% | <25% | Currently 43%. Shift to Senior+ content. |
| Post impressions (avg) | 500 | 1,500 | Secondary to engagement rate. |
| Profile views/week | 50 | 200 | Lagging indicator of content quality. |
| Connection requests sent | 10/week | 15/week | Targeted at Senior+ profiles only. |

### Weekly review checklist

Every Sunday, check:
- [ ] Which post got the most COMMENTS (not impressions)?
- [ ] What question generated replies?
- [ ] What % of viewers were Entry vs Senior+?
- [ ] Did I post all 4 days this week?
- [ ] Did I comment on 5 profiles/day before posting?

---

## Content Repurposing Flow

```
Blog post (chudi.dev)
  -> LinkedIn text post (standalone story, link in first comment)
  -> LinkedIn carousel (only for top-performing topics, not every post)
  -> Dev.to cross-post (canonical to chudi.dev)
```

Newsletter removed from flow until audience threshold met.
Carousels reserved for proven topics (not speculative).

---

## Anti-patterns (NEVER do these)

- "Just published: [title]" (kills reach)
- External link anywhere in the post body (kills reach)
- "Agree or disagree?" as the only CTA (lazy, no one answers)
- Pure data without personal stakes (gets impressions, no engagement)
- More than 4 hashtags
- Em-dashes (use periods or commas instead)
- Starting with "I'm excited to share..." (generic, scrolled past)
- Repeating the same framework more than twice across 8 weeks
- Self-help framing without architectural/systems angle (repels Senior+)
- "Grind" tone, hustle culture, performative toughness (not our voice)
- Failure stories without genuine self-compassion (humble-brags)
- Conclusions without showing the exploration that led there
