# Setup Guide

Step-by-step from zero to a running outreach pipeline.

---

## Step 1: Get Your Free API Keys (15 min)

### Brevo (email sending — 300 emails/day free)
1. Go to [app.brevo.com](https://app.brevo.com/) → Sign up
2. Settings → SMTP & API → API Keys → Create a new API key
3. Copy to `.env` as `BREVO_API_KEY`

### Apollo.io (email finding — 50 credits/month free)
1. Go to [app.apollo.io](https://app.apollo.io/) → Sign up
2. Settings → Integrations → API → Create API key
3. Copy to `.env` as `APOLLO_API_KEY`

### ZeroBounce (email verification — 100 credits/month free)
1. Go to [app.zerobounce.net](https://app.zerobounce.net/) → Sign up
2. API → Copy your API key
3. Copy to `.env` as `ZEROBOUNCE_API_KEY`

### Anthropic (Claude personalization — pay-per-use, ~$0.01 per 100 emails)
1. Go to [console.anthropic.com](https://console.anthropic.com/) → API Keys → Create
2. Copy to `.env` as `ANTHROPIC_API_KEY`

---

## Step 2: Buy a Sending Domain (10 min, $12/year)

**Critical:** Never send cold email from your main domain. A bounce rate spike can blacklist your primary domain permanently.

1. Buy a close variant: if you're `jane.com`, buy `janeoutreach.com` or `hey-jane.com`
2. At your domain registrar (Namecheap, Cloudflare, Google Domains), add these DNS records:

### SPF Record (TXT)
```
Host: @
Value: v=spf1 include:sendinblue.com ~all
TTL: Auto
```

### DKIM Record
1. In Brevo: Settings → Senders & IP → Domains → Add your domain
2. Brevo shows you a DKIM TXT record — paste it into your DNS exactly as shown
3. Click "Authenticate" in Brevo to verify

### DMARC Record (TXT)
```
Host: _dmarc
Value: v=DMARC1; p=none; rua=mailto:dmarc@yourdomain.com
TTL: Auto
```

**Wait 24-48 hours for DNS to propagate before sending any email.**

### Update .env
```
SENDER_EMAIL=you@your-sending-domain.com
SENDER_NAME=Your Name
```

---

## Step 3: Domain Warming (30 days — run in parallel)

Before sending to real prospects, Brevo needs to warm your domain. This happens automatically when you start sending — but you need to start small.

**Brevo warming schedule (they handle this):**
- Week 1: 5-10 emails/day to known-good addresses (send test emails to yourself, Gmail, Outlook)
- Week 2: 20-50/day
- Week 3-4: Up to 300/day

**What to do:** In your first month, send test emails to addresses you control (multiple Gmail, Outlook, iCloud accounts). Don't send to prospects until week 3-4.

Set `DRY_RUN=true` in `.env` for the first 30 days of LinkedIn work — this lets you run and test everything without actually sending emails.

---

## Step 4: Set Up Your ICP List

Open `prospects/icp_list.csv` and fill in your real targets:

```csv
linkedin_url,name,company,title,icp_score,notes
https://www.linkedin.com/in/janesmith,Jane Smith,Acme Corp,Head of Engineering,8,Posted about async Python last week
https://www.linkedin.com/in/johndoe,John Doe,Beta Inc,CTO,9,
```

**Scoring guide:**
- 10: Perfect fit — role, company size, and industry all match
- 8-9: Strong fit — 2 of 3 criteria match
- 6-7: Decent fit — worth connecting, lower priority
- Below 6: Skip

**Finding prospects:** LinkedIn search with filters (title, company size, industry). LinkedIn Sales Navigator free trial gives 50 searches. Export manually to CSV — 20-30 per batch is enough to start.

---

## Step 5: Install and Initialize

```bash
# Clone or unzip the marketing stack
cd marketing-agent

# Install dependencies
pip install anthropic requests

# Copy and fill in credentials
cp .env.example .env
nano .env  # or use your editor

# Initialize database
python3 scripts/init_db.py

# Load your ICP list
python3 scripts/load_prospects.py prospects/icp_list.csv

# Verify
python3 scripts/marketing_resolve.py
```

---

## Step 6: First LinkedIn Session

**Prerequisites:**
- Claude Code installed, OR claude.ai with the Chrome extension
- Chrome browser open, logged into LinkedIn

1. Open `linkedin_session.md` in any text editor
2. Copy the entire SESSION PROMPT section (between the triple backticks)
3. Paste it into a new Claude conversation
4. Follow Claude's instructions — it will navigate Chrome, read profiles, write notes, and send requests

**First session:** Let Claude run through 5-10 profiles. Watch what it does. Make sure the personalization notes feel right for your ICP.

---

## Step 7: Set Up VPS Cron (for autonomous email)

Once your domain is warmed (day 30+), set up cron on any Linux VPS:

```bash
# SSH into your VPS
ssh root@your-vps-ip

# Create log directory
mkdir -p /opt/marketing/logs

# Copy scripts to VPS
scp -r marketing-agent/ root@your-vps-ip:/opt/marketing/

# Edit crontab
crontab -e
```

Add these lines:
```
# Email enrichment: 10am UTC daily
0 10 * * * LEADS_DB_PATH=/opt/marketing/data/marketing_leads.db python3 /opt/marketing/scripts/enrich_lead.py run >> /opt/marketing/logs/enrich.log 2>&1

# Email sequences: 9am UTC daily
0 9 * * * LEADS_DB_PATH=/opt/marketing/data/marketing_leads.db python3 /opt/marketing/scripts/email_sequence.py send >> /opt/marketing/logs/email.log 2>&1
```

The DB path on the VPS must match `LEADS_DB_PATH` in your environment.

---

## Step 8: Run It

**Week 1-4 (domain warming):**
- LinkedIn sessions: 2-3x per week (30 min each)
- Email: dry_run only, no real sends

**Week 5+ (live email):**
- Set `DRY_RUN=false`
- Cron handles email automatically
- LinkedIn sessions continue 2-3x/week
- Check stats weekly: `python3 scripts/marketing_resolve.py`

---

## Troubleshooting

**"DB not found" error:**
Run `python3 scripts/init_db.py` first.

**Apollo returns no email:**
Normal — Apollo's free tier doesn't have every contact. ~60-70% match rate for tech companies. The system silently skips non-matches.

**ZeroBounce returns "catch-all":**
The domain accepts all emails, so verification is inconclusive. These are NOT added to the sequence (protects your domain reputation).

**Brevo send fails:**
Check `BREVO_API_KEY` is set correctly. Check your daily send limit in Brevo dashboard.

**LinkedIn session doesn't seem to be sending:**
Check the daily cap: `sqlite3 data/marketing_leads.db "SELECT * FROM daily_caps WHERE date=date('now')"`. If connections_sent >= 20, cap is hit for today.

**Chrome MCP not responding:**
Make sure the Claude Chrome extension is installed and Chrome is open. Check chrome://extensions/ to confirm the extension is active.

---

## Safety Defaults

| Setting | Default | Change when... |
|---------|---------|----------------|
| `DRY_RUN` | `false` | Set `true` during domain warming |
| `DAILY_EMAIL_CAP` | `50` | Raise after 30+ days of clean sends |
| LinkedIn daily connections | 20 | Never raise above 20 (LinkedIn limit) |
| LinkedIn daily views | 30 | Fine to raise to 50 after 2+ months |
