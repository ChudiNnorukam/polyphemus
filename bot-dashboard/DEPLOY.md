# Dashboard 2-Way Pipeline Deploy Checklist

## Pre-requisites
- SSH access to VPS `82.24.19.114` restored
- Commit `8383c45` pushed to main (DONE)

## Backend Deploy

```bash
# 1. Check SSH works
ssh root@82.24.19.114 "uptime"

# 2. Find where dashboard API lives on VPS
ssh root@82.24.19.114 "find /opt -name 'api.py' -path '*/dashboard*' 2>/dev/null"
# OR: systemctl list-units '*dashboard*'

# 3. Install new dependency
ssh root@82.24.19.114 "pip install pydantic"

# 4. Deploy updated api.py
scp bot-dashboard/api.py root@82.24.19.114:/path/to/dashboard/api.py

# 5. Verify syntax on VPS
ssh root@82.24.19.114 "cd /tmp && python3 -m py_compile /path/to/dashboard/api.py"

# 6. Restart dashboard service
ssh root@82.24.19.114 "systemctl restart dashboard"

# 7. Verify running
ssh root@82.24.19.114 "systemctl is-active dashboard && journalctl -u dashboard --since '30 seconds ago' | grep -iE 'error|started'"

# 8. Test new endpoints
curl -H "Authorization: Bearer $TOKEN" http://82.24.19.114:PORT/api/services
curl -H "Authorization: Bearer $TOKEN" http://82.24.19.114:PORT/api/audit-log
```

## Frontend Deploy
Frontend is gitignored. Files are at:
- `bot-dashboard/frontend/src/app/logs/page.tsx`
- `bot-dashboard/frontend/src/app/services/page.tsx`
- `bot-dashboard/frontend/src/app/ops/page.tsx`
- `bot-dashboard/frontend/src/lib/api.ts` (updated)
- `bot-dashboard/frontend/src/components/nav.tsx` (updated)

Deploy via your Next.js hosting (Vercel or local `npm run build && npm start`).

## New Endpoints Added
| Method | Endpoint | Feature |
|--------|----------|---------|
| GET | /api/logs | Journalctl viewer |
| GET | /api/logs/stream | SSE live log stream |
| POST | /api/control/stop | Stop service |
| POST | /api/control/start | Start service |
| POST | /api/deploy | Deploy files (whitelist + validation) |
| GET | /api/balance | USDC balance |
| GET | /api/process-health | Process metrics |
| GET | /api/config/diff | Config diff vs snapshot |
| GET | /api/audit-log | Audit trail |
| GET | /api/services | All service statuses |

## VPS SSH Recovery (if port 22 still refused)
1. Login to QuantVPS web console/VNC
2. Run: `systemctl status sshd && systemctl start sshd`
3. Check firewall: `ufw status` or `iptables -L -n | grep 22`
4. If needed: `ufw allow 22/tcp && ufw reload`
