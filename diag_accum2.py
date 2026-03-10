#!/usr/bin/env python3
"""Diagnostic: test aiohttp default headers + midpoint fetch."""
import asyncio
import json
import time
import aiohttp

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

async def main():
    ts = int(time.time())
    window = 300
    ts_rounded = int(ts // window) * window

    # Test 1: Does aiohttp get 403 with default User-Agent?
    print("=== Test 1: aiohttp default User-Agent ===")
    async with aiohttp.ClientSession() as session:
        slug = f"btc-updown-5m-{ts_rounded}"
        url = f"{GAMMA}/markets"
        try:
            async with session.get(url, params={"slug": slug}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"Status: {resp.status}")
                print(f"Request headers: {resp.request_info.headers}")
                if resp.status == 200:
                    data = await resp.json()
                    print(f"Markets found: {len(data)}")
                    if data:
                        m = data[0]
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
                        print(f"active={m.get('active')} closed={m.get('closed')}")
                        print(f"tokens={len(tokens)} outcomes={outcomes}")

                        # Test 2: Can we get midpoints?
                        if len(tokens) == 2:
                            print(f"\n=== Test 2: CLOB midpoints ===")
                            for i, tid in enumerate(tokens):
                                mid_url = f"{CLOB}/midpoint?token_id={tid}"
                                try:
                                    async with session.get(mid_url, timeout=aiohttp.ClientTimeout(total=10)) as mid_resp:
                                        print(f"token[{i}] status={mid_resp.status}")
                                        if mid_resp.status == 200:
                                            mid_data = await mid_resp.json()
                                            print(f"  midpoint data: {mid_data}")
                                        else:
                                            body = await mid_resp.text()
                                            print(f"  body: {body[:200]}")
                                except Exception as e:
                                    print(f"  token[{i}] error: {e}")

                            # Test 3: Pair cost calculation
                            print(f"\n=== Test 3: Pair cost ===")
                            up_mid = None
                            down_mid = None
                            for i, tid in enumerate(tokens):
                                mid_url = f"{CLOB}/midpoint?token_id={tid}"
                                try:
                                    async with session.get(mid_url, timeout=aiohttp.ClientTimeout(total=10)) as mid_resp:
                                        if mid_resp.status == 200:
                                            mid_data = await mid_resp.json()
                                            mid_val = float(mid_data.get("mid", 0))
                                            label = outcomes[i] if i < len(outcomes) else f"token_{i}"
                                            print(f"  {label}: midpoint = {mid_val}")
                                            if "up" in label.lower():
                                                up_mid = mid_val
                                            else:
                                                down_mid = mid_val
                                except Exception as e:
                                    print(f"  Error: {e}")

                            if up_mid is not None and down_mid is not None:
                                pair_cost = up_mid + down_mid
                                print(f"\n  PAIR COST = {pair_cost:.4f}")
                                print(f"  Max allowed = 0.975")
                                print(f"  Would enter? {pair_cost < 0.975}")
                            else:
                                print("\n  Could not calculate pair cost")
                else:
                    body = await resp.text()
                    print(f"Response body: {body[:300]}")
        except Exception as e:
            print(f"ERROR: {e}")

asyncio.run(main())
