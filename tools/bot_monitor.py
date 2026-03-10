"""
Unified Bot Monitor — Rich TUI
Polls both VPS bot dashboards via SSH-tunneled HTTP API.

Setup (run once):
    bash tools/start_tunnels.sh

Run:
    python3 tools/bot_monitor.py
"""

import asyncio
import time
from datetime import datetime

import aiohttp
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

BOTS = [
    {
        "name": "Polyphemus-P",
        "label": "POLYPHEMUS",
        "host": "localhost",
        "port": 8080,
        "strategy": "ACCUM",
        "wallet": "Proxy",
        "vps": "142.93.143.178",
    },
    {
        "name": "Lagbot-E",
        "label": "LAGBOT",
        "host": "localhost",
        "port": 8081,
        "strategy": "ACCUM+ARB",
        "wallet": "EOA",
        "vps": "82.24.19.114",
    },
]

POLL_INTERVAL = 5
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=3)


async def poll_bot(session: aiohttp.ClientSession, bot: dict) -> dict:
    base = f"http://{bot['host']}:{bot['port']}"
    try:
        status_r, balance_r, accum_r = await asyncio.gather(
            session.get(f"{base}/api/status", timeout=REQUEST_TIMEOUT),
            session.get(f"{base}/api/balance", timeout=REQUEST_TIMEOUT),
            session.get(f"{base}/api/accumulator", timeout=REQUEST_TIMEOUT),
        )
        return {
            "status": await status_r.json(),
            "balance": await balance_r.json(),
            "accum": await accum_r.json(),
            "error": None,
            "ts": time.time(),
        }
    except Exception as e:
        return {"error": str(e), "ts": time.time()}


def pnl_color(val: float) -> str:
    if val > 0:
        return "green"
    if val < 0:
        return "red"
    return "dim"


def fmt_pnl(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}${val:.2f}"


def state_style(state: str) -> str:
    return {
        "hedged": "bold green",
        "accumulating": "bold yellow",
        "settling": "bold cyan",
        "scanning": "dim",
        "idle": "dim",
    }.get(state.lower(), "white")


def render_bot_panel(cfg: dict, data: dict) -> Panel:
    label = cfg["label"]
    wallet = cfg["wallet"]
    strategy = cfg["strategy"]
    vps = cfg["vps"]

    if data.get("error"):
        content = Text(f"\nOFFLINE\n{data['error']}", style="bold red", justify="center")
        return Panel(
            content,
            title=f"[bold red]{label}[/bold red]",
            subtitle=f"[dim]{vps}[/dim]",
            border_style="red",
        )

    status = data["status"]
    balance = data["balance"]
    accum = data["accum"]

    uptime = status.get("uptime_hours", 0)
    errors = status.get("errors", 0)
    bal = balance.get("balance", 0.0)
    deployed = balance.get("deployed", 0.0)
    available = balance.get("available", 0.0)

    accum_state = accum.get("state", "idle") if accum.get("enabled") else "disabled"
    total_pnl = accum.get("total_pnl", 0.0)
    hedged_count = accum.get("hedged_count", 0)
    orphaned_count = accum.get("orphaned_count", 0)
    active_pos = accum.get("active_positions", 0)
    max_concurrent = accum.get("max_concurrent", 1)
    best_bid = accum.get("best_bid_pair", 0.0)
    settlements = accum.get("settlements", [])

    # Status dot
    if errors > 5:
        dot = "[red]●[/red]"
        status_txt = "ERROR"
    else:
        dot = "[green]●[/green]"
        status_txt = "LIVE"

    # Main metrics table
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", style="dim", width=14)
    t.add_column(justify="left")

    t.add_row("Status", f"{dot} {status_txt}  [dim]{uptime:.1f}h uptime[/dim]")
    t.add_row("Wallet", f"[dim]{wallet}[/dim]  [dim]{vps}[/dim]")
    t.add_row("Strategy", f"[cyan]{strategy}[/cyan]")
    t.add_row("", "")
    t.add_row("Balance", f"[bold white]${bal:.2f}[/bold white]")
    t.add_row("Deployed", f"[yellow]${deployed:.2f}[/yellow]")
    t.add_row("Available", f"[dim]${available:.2f}[/dim]")
    t.add_row("", "")

    pnl_txt = Text(fmt_pnl(total_pnl), style=f"bold {pnl_color(total_pnl)}")
    state_txt = Text(accum_state.upper(), style=state_style(accum_state))

    t.add_row("ACCUM PnL", pnl_txt)
    t.add_row("State", state_txt)
    t.add_row("Positions", f"[white]{active_pos}/{max_concurrent}[/white] active")
    t.add_row("Best Bid", f"[dim]${best_bid:.4f}[/dim]")
    unwound_count = accum.get("unwound_count", 0)
    t.add_row(
        "Settled",
        f"[green]{hedged_count}H[/green]  [yellow]{unwound_count}U[/yellow]  [red]{orphaned_count}O[/red]",
    )

    # Last 3 settlements
    if settlements:
        t.add_row("", "")
        t.add_row("Recent", "[dim]slug | pnl[/dim]")
        for s in settlements[-3:]:
            slug = s.get("slug", "?").split("-")
            short = "-".join(slug[-3:]) if len(slug) >= 3 else s.get("slug", "?")
            spnl = s.get("pnl", 0.0)
            color = pnl_color(spnl)
            t.add_row("", f"[dim]{short}[/dim]  [{color}]{fmt_pnl(spnl)}[/{color}]")

    border = "green" if errors <= 5 else "red"
    return Panel(
        t,
        title=f"[bold]{label}[/bold]",
        subtitle=f"[dim]:{cfg['port']}[/dim]",
        border_style=border,
        padding=(1, 2),
    )


def render_footer(all_data: list, last_refresh: float) -> Panel:
    total_balance = sum(
        d.get("balance", {}).get("balance", 0.0) for d in all_data if not d.get("error")
    )
    total_pnl = sum(
        d.get("accum", {}).get("total_pnl", 0.0) for d in all_data if not d.get("error")
    )
    total_pos = sum(
        d.get("accum", {}).get("active_positions", 0) for d in all_data if not d.get("error")
    )
    total_hedged = sum(
        d.get("accum", {}).get("hedged_count", 0) for d in all_data if not d.get("error")
    )

    now = datetime.now().strftime("%H:%M:%S")
    age = int(time.time() - last_refresh)

    t = Table.grid(padding=(0, 4))
    t.add_column()
    t.add_column()
    t.add_column()
    t.add_column()
    t.add_column(justify="right")

    pnl_color_str = pnl_color(total_pnl)
    t.add_row(
        f"[dim]Combined Balance:[/dim] [bold white]${total_balance:.2f}[/bold white]",
        f"[dim]Total PnL:[/dim] [bold {pnl_color_str}]{fmt_pnl(total_pnl)}[/bold {pnl_color_str}]",
        f"[dim]Active Positions:[/dim] [white]{total_pos}[/white]",
        f"[dim]Cycles Total:[/dim] [white]{total_hedged}[/white]",
        f"[dim]Refreshed {now} ({age}s ago)[/dim]",
    )

    return Panel(t, border_style="dim", padding=(0, 1))


def render_header() -> Text:
    t = Text(justify="center")
    t.append("POLYMARKET BOT MONITOR", style="bold white")
    t.append("  |  ", style="dim")
    t.append(datetime.now().strftime("%Y-%m-%d"), style="dim")
    return t


async def main():
    console = Console()
    console.print("\n[bold]Starting Bot Monitor...[/bold]")
    console.print("[dim]Connecting to SSH tunnels on :8080 (Polyphemus) and :8081 (Arges)[/dim]")
    console.print("[dim]Run [bold]bash tools/start_tunnels.sh[/bold] if tunnels are not active\n[/dim]")

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=5),
    )
    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    last_refresh = time.time()
    all_data: list = [{}, {}]

    async with aiohttp.ClientSession() as session:
        with Live(layout, console=console, refresh_per_second=2, screen=True):
            while True:
                results = await asyncio.gather(
                    *[poll_bot(session, bot) for bot in BOTS]
                )
                all_data = list(results)
                last_refresh = time.time()

                layout["header"].update(
                    Panel(render_header(), border_style="dim", padding=(0, 2))
                )
                layout["left"].update(render_bot_panel(BOTS[0], all_data[0]))
                layout["right"].update(render_bot_panel(BOTS[1], all_data[1]))
                layout["footer"].update(render_footer(all_data, last_refresh))

                await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
