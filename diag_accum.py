#!/usr/bin/env python3
"""Diagnostic: check what the accumulator sees."""
import time
import json
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"

ts = int(time.time())
window = 300
ts_rounded = int(ts // window) * window

print(f"Current time: {ts}")
print(f"Rounded: {ts_rounded}")
print()

found_any = False
for offset in range(4):
    epoch = ts_rounded + (offset * window)
    slug = f"btc-updown-5m-{epoch}"
    url = f"{GAMMA}/markets?slug={slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data:
            found_any = True
            m = data[0]
            active = m.get("active")
            closed = m.get("closed")
            end = m.get("endDate")
            print(f"{slug}:")
            print(f"  active={active}  closed={closed}  endDate={end}")

            raw_tokens = m.get("clobTokenIds", "[]")
            if isinstance(raw_tokens, str):
                tokens = json.loads(raw_tokens)
            else:
                tokens = raw_tokens

            raw_outcomes = m.get("outcomes", "[]")
            if isinstance(raw_outcomes, str):
                outcomes = json.loads(raw_outcomes)
            else:
                outcomes = raw_outcomes

            print(f"  tokens={len(tokens)}  outcomes={outcomes}")
            if len(tokens) == 2:
                print(f"  token[0]={tokens[0][:16]}...")
                print(f"  token[1]={tokens[1][:16]}...")
        else:
            print(f"{slug}: EMPTY (no market)")
    except Exception as e:
        print(f"{slug}: ERROR {e}")

if not found_any:
    print("\nNO MARKETS FOUND for any slug offset.")
    print("Trying broader search...")
    url = f"{GAMMA}/markets?tag=updown&closed=false&limit=5"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        print(f"\nBroader search returned {len(data)} markets:")
        for m in data[:5]:
            print(f"  slug={m.get('slug')}  active={m.get('active')}  end={m.get('endDate')}")
    except Exception as e:
        print(f"Broader search ERROR: {e}")

    # Also try the exact slug format gabagool uses
    print("\nTrying alternative slug patterns...")
    for pattern in [
        f"btc-updown-5m-{ts_rounded}",
        f"bitcoin-updown-5m-{ts_rounded}",
        f"btc-5m-updown-{ts_rounded}",
        f"will-btc-go-up-or-down-5m-{ts_rounded}",
    ]:
        url = f"{GAMMA}/markets?slug={pattern}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            if data:
                print(f"  FOUND: {pattern} -> {data[0].get('question', 'N/A')[:80]}")
            else:
                print(f"  MISS:  {pattern}")
        except Exception as e:
            print(f"  ERROR: {pattern} -> {e}")
