# LinkedIn Session — Claude Prompt Card

Paste this into a Claude conversation to run a LinkedIn engagement session.
Claude will drive Chrome MCP to view profiles, engage with posts, and send
connection requests. Results are written to the VPS DB via SSH.

---

## SESSION PROMPT (paste into Claude)

```
Run a LinkedIn outreach session for me. Follow these steps exactly:

**Step 1: Check today's cap**
SSH to VPS and check how many connections we've sent today:
ssh root@82.24.19.114 'sqlite3 /opt/openclaw/data/marketing_leads.db "SELECT COALESCE(connections_sent,0) FROM daily_caps WHERE date=date(\"now\")"'

If connections_sent >= 20, stop and report: "Daily cap reached."

**Step 2: Get next prospects**
ssh root@82.24.19.114 'sqlite3 /opt/openclaw/data/marketing_leads.db "SELECT id, linkedin_url, name, company, title, notes FROM leads WHERE status IN (\"prospect\",\"viewed\") AND connection_sent_at IS NULL ORDER BY icp_score DESC LIMIT 15"'

**Step 3: For each prospect (up to daily cap)**
Navigate to their LinkedIn URL via browser.
For each profile:
  a. View the profile for 3-5 seconds (dwell time)
  b. Check for posts in last 7 days — if relevant to their role, like one post
  c. Note any personalization hooks (recent post topic, company news, shared connection)

**Step 4: Send connection requests**
For prospects worth connecting with (skip if clearly wrong ICP):
- Generate a personalized 2-sentence connection note:
  * Sentence 1: Reference something specific (their post, company, mutual connection, role)
  * Sentence 2: Brief reason to connect — no pitches, no "I wanted to reach out"
  * Keep under 200 characters total
- Send the connection request with the note via LinkedIn UI

**Step 5: Update DB after each action**
After viewing each profile:
ssh root@82.24.19.114 'sqlite3 /opt/openclaw/data/marketing_leads.db "UPDATE leads SET profile_viewed_at=datetime(\"now\"), status=\"viewed\" WHERE id=<ID>"'

After sending each connection request:
ssh root@82.24.19.114 'sqlite3 /opt/openclaw/data/marketing_leads.db "UPDATE leads SET connection_sent_at=datetime(\"now\"), status=\"pending\" WHERE id=<ID>"'
ssh root@82.24.19.114 'sqlite3 /opt/openclaw/data/marketing_leads.db "INSERT INTO daily_caps(date,connections_sent) VALUES(date(\"now\"),1) ON CONFLICT(date) DO UPDATE SET connections_sent=connections_sent+1"'

**Step 6: Check for accepted connections**
Navigate to LinkedIn > My Network > Manage > Sent.
For each profile showing "Message" instead of "Pending":
ssh root@82.24.19.114 'sqlite3 /opt/openclaw/data/marketing_leads.db "UPDATE leads SET connection_accepted_at=datetime(\"now\"), connection_checked_at=datetime(\"now\"), status=\"connected\" WHERE linkedin_url=\"<URL>\""'

**Step 7: End of session report**
Run: ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 /opt/openclaw/scripts/marketing_resolve.py'

Show me the output.
```

---

## DAILY LIMITS
- Profile views: 30 max
- Connection requests: 20 max
- DMs to connections: 10 max
- Run during: 9 AM - 6 PM local time only (human hours)

## CONNECTION NOTE EXAMPLES

Good:
> "Saw your post on async-first engineering teams — exactly the problem we're tackling. Would love to connect."

Good:
> "Building in public the right way — your thread on [topic] was sharp. Fellow builder here, worth connecting."

Bad:
> "Hi, I came across your profile and wanted to connect to discuss opportunities..."
> "I'd love to tell you about what we do at [company]..."

## AFTER THE SESSION
Run locally to see full stats:
```
cd ~/Projects/business/marketing-agent
python3 scripts/marketing_resolve.py
```
