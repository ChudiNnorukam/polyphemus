#!/usr/bin/env python3
"""
bot_monitor.py — Unified live dashboard for Polyphemus + Lagbot
Requires: pip install rich aiohttp
Setup:    bash tools/start_tunnels.sh
Run:      python3 tools/bot_monitor.py
"""

import asyncio
import aiohttp
from datetime import datetime

from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

BOTS = [
    {
        "name": "Polyphemus",
        "label": "PROXY",
        "host": "localhost",
        "port": 8080,
        "vps": "142.93.143.178",
        "strategy": "ACCUM",
    },
    {
        "name": "Lagbot",
        "label": "EOA",
        "host": "localhost",
        "port": 8081,
        "vps": "82.24.19.114",
        "strategy": "ARB+ACCUM",
    },
]

POLL_INTERVAL = 5


async def poll_bot(session: aiohttp.ClientSession, bot: dict) -> dict:
    base = f"http://{bot['host']}:{bot['port']}"
    try:
        async with asyncio.timeout(3):
            sr = await session.get(f"{base}/api/status")
            br = await session.get(f"{base}/api/balance")
            ar = await session.get(f"{base}/api/accumulator")
            status = await sr.json()
            balance = await br.json()
            accum = await ar.json()
        return {"status": status, "balance": balance, "accum": accum, "error": None}
    except Exception as e:
        return {"status": {}, "balance": {}, "accum": {}, "error": str(e)[:60]}


def fmt_pnl(val) -> Text:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return Text("n/a", style="dim")
    if v > 0:
        return Text(f"+${v:.2f}", style="bold green")
    elif v < 0:
        return Text(f"-${abs(v):.2f}", style="bold red")
    return Text(f"$0.00", style="dim")


def fmt_state(state: str) -> Text:
    colors = {
        "IDLE": "dim",
        "SCANNING": "cyan",
        "ACCUMULATING": "yellow",
        "HEDGED": "bold green",
        "SETTLING": "magenta",
    }
    return Text(state or "?", style=colors.get(state, "white"))


def render_bot_panel(cfg: dict, data: dict) -> Panel:
    st = data.get("status", {})
    bal = data.get("balance", {})
    acc = data.get("accum", {})
    err = data.get("error")

    t = Table(box=box.SIMPLE, padding=(0, 1), show_header=False, show_edge=False)
    t.add_column("key", style="dim", width=22)
    t.add_column("val", width=20)

    if err:
        t.add_row("Status", Text("OFFLINE", style="bold red"))
        t.add_row("Error", Text(err, style="red"))
    else:
        uptime = st.get("uptime_hours", 0)
        errors = st.get("errors", [])
        status_text = Text("LIVE", style="bold green") if not errors else Text("DEGRADED", style="yellow")
        t.add_row("Status", status_text)
        t.add_row("Uptime", f"{float(uptime):.1f}h" if uptime else "?")
        t.add_row("", "")

        b = float(bal.get("balance", 0))
        dep = float(bal.get("deployed", 0))
        avail = float(bal.get("available", 0))
        t.add_row("Balance", f"[bold]${b:.2f}[/bold]")
        t.add_row("Deployed", f"${dep:.2f}")
        t.add_row("Available", f"${avail:.2f}")
        t.add_row("", "")

        state = acc.get("state", "?").upper()
        active = acc.get("active_positions", 0)
        max_c = acc.get("max_concurrent", 3)
        hedged = acc.get("hedged_count", 0)
        unwound = acc.get("unwound_count", 0)
        orphaned = acc.get("orphaned_count", 0)
        pnl = acc.get("total_pnl", 0)
        bid_pair = acc.get("best_bid_pair")
        consec = acc.get("consecutive_unwinds", 0)

        wr = hedged / max(hedged + unwound, 1)

        t.add_row("Accum State", fmt_state(state))
        t.add_row("Active Pairs", f"{active}/{max_c}")
        t.add_row("Circuit PnL", fmt_pnl(pnl))
        t.add_row("Win Rate", f"{wr:.0%} ({hedged}H/{unwound}U)")
        t.add_row("Orphaned", str(orphaned))
        t.add_row("Consec Unwinds", str(consec))
        if bid_pair is not None:
            t.add_row("Best Bid Pair", f"${float(bid_pair):.4f}")

        # Last 2 settlements
        settlements = acc.get("settlements", [])
        if settlements:
            t.add_row("", "")
            t.add_row("[dim]Recent Settles[/dim]", "")
            for s in settlements[-2:]:
                slug = s.get("slug", "?")[-18:]
                s_pnl = s.get("pnl", 0)
                reason = s.get("exit_reason", "?")[:6]
                t.add_row(f"  {slug}", f"{reason} {fmt_pnl(s_pnl).plain}")

    title = (
        f"[bold]{cfg['name']}[/bold]  "
        f"[dim]{cfg['label']} | {cfg['vps']} | {cfg['strategy']}[/dim]"
    )
    border = "red" if err else ("green" if not data.get("accum", {}).get("consecutive_unwinds", 0) >= 3 else "yellow")
    return Panel(t, title=title, border_style=border)


def render_footer(all_data: list) -> Panel:
    total_bal = 0.0
    total_pnl = 0.0
    total_pos = 0
    total_hedged = 0
    total_unwound = 0
    errors = 0

    for d in all_data:
        if d.get("error"):
            errors += 1
            continue
        try:
            total_bal += float(d["balance"].get("balance", 0))
            total_pnl += float(d["accum"].get("total_pnl", 0))
            total_pos += int(d["accum"].get("active_positions", 0))
            total_hedged += int(d["accum"].get("hedged_count", 0))
            total_unwound += int(d["accum"].get("unwound_count", 0))
        except (TypeError, ValueError):
            pass

    wr = total_hedged / max(total_hedged + total_unwound, 1)
    now = datetime.now().strftime("%H:%M:%S")
    pnl_str = fmt_pnl(total_pnl).plain

    parts = [
        f"[dim]{now}[/dim]",
        f"Combined Balance: [bold]${total_bal:.2f}[/bold]",
        f"Combined PnL: {pnl_str}",
        f"Active Pairs: [bold]{total_pos}[/bold]",
        f"WR: {wr:.0%} ({total_hedged}H/{total_unwound}U)",
    ]
    if errors:
        parts.append(f"[red]{errors} OFFLINE[/red]")

    return Panel(Text.from_markup("  |  ".join(parts)), height=3, border_style="dim")


async def main():
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        layout = Layout()
        layout.split_column(
            Layout(name="bots", ratio=9),
            Layout(name="footer", size=5),
        )
        layout["bots"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        with Live(layout, refresh_per_second=1, screen=True):
            while True:
                try:
                    results = await asyncio.gather(
                        *[poll_bot(session, bot) for bot in BOTS]
                    )
                    layout["left"].update(render_bot_panel(BOTS[0], results[0]))
                    layout["right"].update(render_bot_panel(BOTS[1], results[1]))
                    layout["footer"].update(render_footer(results))
                except Exception:
                    pass
                await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
