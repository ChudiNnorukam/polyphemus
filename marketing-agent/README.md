# Claude Code Marketing Stack

**LinkedIn + email outreach system for technical builders. No SaaS subscriptions. No account bans. Full control.**

Built on the same Python/SQLite/cron pattern as production trading bots — designed to run indefinitely without babysitting.

---

## What You Get

| Component | What it does |
|-----------|-------------|
| `linkedin_session.md` | Claude prompt card — paste into Claude, it drives Chrome to view profiles, like posts, and send AI-personalized connection requests |
| `scripts/enrich_lead.py` | Finds + verifies emails via Apollo.io + ZeroBounce (free tier covers 50 leads/month) |
| `scripts/email_sequence.py` | Fires a 4-email PAS cadence via Brevo API. Runs daily from cron. Claude personalizes each email. |
| `scripts/marketing_resolve.py` | Full-funnel stats: views, connections, emails sent, replies, conversions |
| `scripts/check_accepted.py` | Track and mark accepted LinkedIn connections |
| `scripts/load_prospects.py` | Import your ICP list from CSV into the lead DB |
| `scripts/init_db.py` | Create the SQLite database (run once) |
| `templates/email_templates.json` | 4 battle-tested PAS email templates (Problem, Social Proof, Insight, Breakup) |
| `prospects/icp_list.csv` | Template CSV — fill with your targets |

**Stack:** Python 3.11+, SQLite, Brevo (email), Apollo.io (email finding), ZeroBounce (verification), Claude haiku-4-5 (personalization), Chrome MCP (LinkedIn).

**Monthly cost at free tiers: ~$0.** The only cost is your Anthropic API key for email personalization (fractions of a cent per email).

---

## Why This Exists

Every LinkedIn outreach tool automates at the API level. LinkedIn detects that. Accounts get banned. You lose months of relationship-building.

This system uses Claude Code's Chrome MCP — a browser extension that drives a real Chrome session. It looks exactly like a human navigating LinkedIn, because it IS a human navigating LinkedIn (with Claude doing the thinking). No bot signatures. No API calls. No bans.

The email side runs autonomously — daily cron on your VPS, Brevo API, Claude personalizes each email for each lead.

---

## How It Works

### Two-channel architecture

**Channel 1: LinkedIn (semi-manual, 30 min/session)**
1. Paste `linkedin_session.md` into a Claude conversation (Claude Code or claude.ai with Chrome extension)
2. Claude checks your daily cap via SSH to your VPS
3. Claude navigates to each prospect's LinkedIn profile
4. Reads their recent posts, notes personalization hooks
5. Generates a 2-sentence connection note referencing something specific
6. Sends the connection request
7. Updates your SQLite DB via SSH after each action

**Channel 2: Email (fully autonomous)**
1. VPS cron runs `enrich_lead.py` daily — finds emails for new connections via Apollo.io
2. ZeroBounce verifies each email before it enters the sequence
3. VPS cron runs `email_sequence.py` daily — fires PAS emails on schedule
4. Claude haiku writes a personalized version for each lead (title, company, pain point)
5. Brevo sends from your domain (SPF/DKIM/DMARC — setup guide included)

### Lead lifecycle
```
prospect → viewed → pending → connected → email_seq → [replied | churned | converted]
```

---

## Quick Start (15 minutes)

### 1. Install dependencies

```bash
pip install anthropic requests
```

### 2. Copy and fill in credentials

```bash
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, APOLLO_API_KEY, ZEROBOUNCE_API_KEY, BREVO_API_KEY
# Fill in: SENDER_EMAIL, SENDER_NAME
```

### 3. Initialize the database

```bash
python3 scripts/init_db.py
```

### 4. Load your first prospects

Edit `prospects/icp_list.csv` with your targets, then:

```bash
python3 scripts/load_prospects.py prospects/icp_list.csv
```

### 5. Check what's ready

```bash
python3 scripts/marketing_resolve.py
```

### 6. Run a LinkedIn session

Open a Claude conversation with Chrome MCP. Paste the contents of `linkedin_session.md`. Follow Claude's lead.

### 7. Set up VPS cron (optional but recommended)

```
# Email enrichment: 10am UTC daily
0 10 * * * LEADS_DB_PATH=/your/path/marketing_leads.db python3 /your/path/enrich_lead.py run >> /var/log/enrich.log 2>&1

# Email sequence: 9am UTC daily
0 9 * * * LEADS_DB_PATH=/your/path/marketing_leads.db python3 /your/path/email_sequence.py send >> /var/log/email.log 2>&1
```

---

## Daily Limits (safe defaults)

| Action | Limit | Why |
|--------|-------|-----|
| Profile views | 30/day | LinkedIn safety |
| Connection requests | 20/day | LinkedIn safety |
| Emails sent | 50/day | Brevo free tier = 300/day; conservative for domain warming |

---

## Commands Reference

```bash
# Funnel stats
python3 scripts/marketing_resolve.py

# LinkedIn connection tracking
python3 scripts/check_accepted.py           # List pending
python3 scripts/check_accepted.py accept 5  # Mark ID 5 as accepted
python3 scripts/check_accepted.py stale     # Show requests > 14 days old

# Email enrichment
python3 scripts/enrich_lead.py scan         # Who needs enrichment?
python3 scripts/enrich_lead.py run          # Find + verify emails
python3 scripts/enrich_lead.py credits      # Check API credit usage

# Email sequences
python3 scripts/email_sequence.py scan      # Who gets emails today?
python3 scripts/email_sequence.py send      # Fire due emails
python3 scripts/email_sequence.py status    # Full sequence view
python3 scripts/email_sequence.py replies   # Poll Brevo for replies
```

Or use the entrypoint:
```bash
./run.sh marketing_resolve
./run.sh email_sequence send
./run.sh enrich_lead run
```

---

## What You Need

- Python 3.11+
- Claude Code or claude.ai with Chrome extension (for LinkedIn sessions)
- Anthropic API key (claude.ai Pro or API)
- [Brevo account](https://app.brevo.com/) — free, 300 emails/day
- [Apollo.io account](https://app.apollo.io/) — free, 50 email credits/month
- [ZeroBounce account](https://app.zerobounce.net/) — free, 100 verifications/month
- A sending domain (buy one for $12/year — setup guide in `SETUP.md`)
- Optional: VPS for autonomous cron (any $5/month VPS works)

---

## ICP CSV Format

```csv
linkedin_url,name,company,title,icp_score
https://www.linkedin.com/in/example,Jane Smith,Acme Corp,Head of Engineering,8
```

`icp_score` is 1-10. Higher scores get processed first. Add `notes` column for personalization hints (recent news, mutual connections, etc.).

---

## License

Single-user license. Do not redistribute. For team use, contact for multi-seat pricing.
