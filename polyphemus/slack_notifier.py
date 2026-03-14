"""Slack notifier for lagbot trade events.

Supports two modes:
- Bot Token API (SLACK_BOT_TOKEN + SLACK_CHANNEL_ID) - uses chat.postMessage
- Incoming Webhook (SLACK_WEBHOOK_URL) - simple POST to webhook URL

Bot Token mode is preferred when a token already exists.
"""

import logging
import threading
from urllib.request import Request, urlopen
import json


logger = logging.getLogger("polyphemus.slack")

# Human-readable labels for exit reason codes
_EXIT_REASON_LABELS = {
    "stop_loss": "Stop Loss",
    "mid_price_stop": "Mid-Price Stop",
    "profit_target": "Profit Target",
    "profit_target_early": "Early Profit",
    "time_exit": "Time Exit",
    "pre_resolution_exit": "Pre-Resolution",
    "oracle_reversal": "Oracle Reversal",
    "oracle_flip": "Oracle Flip",
    "reversal_short": "Reversal Short",
    "hold_to_resolution": "Resolution",
    "market_resolved": "Resolved (Win)",
    "redeemed_loss": "Resolved (Loss)",
    "insufficient_shares": "Insuf. Shares",
    "manual": "Manual",
    "circuit_breaker": "Circuit Breaker",
    "flat_regime": "Flat Regime Exit",
}


def _parse_slug(slug: str) -> tuple:
    """Extract asset and direction from slug like 'btc-updown-5m-1770944400'.

    Returns (asset, direction) or ("?", "?") if unparseable.
    """
    if not slug or slug.startswith("orphan:"):
        return ("?", "?")
    parts = slug.split("-")
    if len(parts) >= 3:
        asset = parts[0].upper()
        direction = "Up" if "up" in slug.lower() and "down" not in slug.lower() else "Down" if "down" in slug.lower() and "up" not in slug.lower() else parts[1].capitalize()
        return (asset, direction)
    return ("?", "?")


def _fmt_hold(secs: float) -> str:
    """Format hold time as '2m 34s' or '45s'."""
    if secs <= 0:
        return ""
    secs = int(secs)
    if secs >= 60:
        return f"{secs // 60}m {secs % 60:02d}s"
    return f"{secs}s"


def _fmt_reason(reason: str) -> str:
    return _EXIT_REASON_LABELS.get(reason, reason)


class SlackNotifier:
    """Fire-and-forget Slack notifications.

    Non-blocking: posts are sent in a background thread so they never
    delay trading operations. All errors are logged and swallowed.
    """

    def __init__(
        self,
        webhook_url: str = "",
        instance_name: str = "lagbot",
        bot_token: str = "",
        channel_id: str = "",
    ):
        self._webhook_url = webhook_url.strip() if webhook_url else ""
        self._bot_token = bot_token.strip() if bot_token else ""
        self._channel_id = channel_id.strip() if channel_id else ""
        self._instance = instance_name

        # Prefer bot token mode if both token and channel are set
        if self._bot_token and self._channel_id:
            self._mode = "bot"
        elif self._webhook_url:
            self._mode = "webhook"
        else:
            self._mode = None

        self._enabled = self._mode is not None

        # Running stats (reset on process start, not persisted)
        self._wins = 0
        self._losses = 0
        self._total_pnl = 0.0
        self._start_balance = 0.0
        self._current_balance = 0.0
        if self._enabled:
            logger.info(f"Slack notifier enabled: mode={self._mode}, instance={instance_name}")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def seed_stats(self, wins: int, losses: int, total_pnl: float, start_balance: float = 0.0):
        """Seed session stats from DB on startup so running totals are accurate."""
        self._wins = wins
        self._losses = losses
        self._total_pnl = total_pnl
        if start_balance > 0:
            self._start_balance = start_balance
            self._current_balance = start_balance
        logger.info(f"Slack stats seeded: {wins}W {losses}L, ${total_pnl:.2f}, bal=${start_balance:.2f}")

    def update_balance(self, balance: float):
        """Update current balance for session line display."""
        self._current_balance = balance

    def _session_line(self) -> str:
        total = self._wins + self._losses
        wr = (self._wins / total * 100) if total > 0 else 0
        sign = "+" if self._total_pnl >= 0 else ""
        line = f"{self._wins}W {self._losses}L ({wr:.0f}%) | {sign}${self._total_pnl:.2f}"
        if self._start_balance > 0 and self._current_balance > 0:
            delta_pct = (self._current_balance - self._start_balance) / self._start_balance * 100
            sign_d = "+" if delta_pct >= 0 else ""
            line += f" | ${self._start_balance:.0f} -> ${self._current_balance:.0f} ({sign_d}{delta_pct:.1f}%)"
        return line

    def notify_entry(
        self,
        slug: str,
        asset: str,
        direction: str,
        entry_price: float,
        size_usd: float = 0.0,
        shares: float = 0.0,
        momentum_pct: float = 0.0,
        source: str = "",
        secs_left: int = 0,
        entry_mode: str = "",
        balance: float = 0.0,
    ):
        if not self._enabled:
            return
        if not asset or not direction:
            asset, direction = _parse_slug(slug)

        if size_usd == 0.0 and shares > 0 and entry_price > 0:
            size_usd = entry_price * shares

        if balance > 0:
            self._current_balance = balance

        payout = shares * (1.0 - entry_price) if entry_price < 1.0 else 0.0
        payout_pct = (1.0 - entry_price) / entry_price * 100 if entry_price > 0 else 0.0

        # Source / signal tag
        if source and "snipe" in source:
            source_tag = f"SNIPE  |  {secs_left}s left"
        elif source and "reversal" in source:
            source_tag = "REVERSAL FLIP"
        elif source and "oracle_flip" in source:
            source_tag = "ORACLE FLIP"
        elif source and "flat_regime" in source:
            delta_str = f"  |  delta {momentum_pct:+.3%}" if momentum_pct else ""
            source_tag = f"RTDS{delta_str}  |  {secs_left}s left" if secs_left else f"RTDS{delta_str}"
        elif momentum_pct:
            source_tag = f"MOM {momentum_pct:+.2%}"
        else:
            source_tag = source or "momentum"

        mode_tag = f"  |  {entry_mode.upper()}" if entry_mode else ""

        bal_line = f"\n_Balance: ${self._current_balance:.2f}_" if self._current_balance > 0 else ""

        msg = (
            f":zap: *BUY* [{self._instance}]\n"
            f"*{asset} {direction}* @ {entry_price:.3f}{mode_tag}\n"
            f"${size_usd:.2f}  ({shares:.0f} sh)  |  {source_tag}\n"
            f"Payout if WIN: +${payout:.2f} ({payout_pct:.0f}%)"
            f"{bal_line}"
        )
        self._post(msg)

    def notify_exit(
        self,
        slug: str,
        asset: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        shares: float,
        pnl: float,
        exit_reason: str,
        hold_secs: float = 0,
        market_title: str = "",
        balance: float = 0.0,
    ):
        if not self._enabled:
            return

        if not asset or not direction:
            asset, direction = _parse_slug(slug)

        is_win = pnl > 0
        if is_win:
            self._wins += 1
        else:
            self._losses += 1
        self._total_pnl += pnl

        if balance > 0:
            self._current_balance = balance

        icon = ":white_check_mark:" if is_win else ":x:"
        outcome = "WIN" if is_win else "LOSS"
        sign = "+" if pnl >= 0 else ""

        # PnL as % of entry cost
        cost = entry_price * shares
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
        pnl_pct_str = f" ({sign}{pnl_pct:.1f}%)" if cost > 0 else ""

        hold_str = f"  |  held {_fmt_hold(hold_secs)}" if hold_secs > 0 else ""
        reason_str = _fmt_reason(exit_reason)

        title_line = f"\n_{market_title[:80]}_" if market_title else ""

        msg = (
            f"{icon} *{outcome}* [{self._instance}]{title_line}\n"
            f"*{asset} {direction}*  {entry_price:.3f} -> {exit_price:.3f}"
            f"  |  {sign}${pnl:.2f}{pnl_pct_str}  ({reason_str}){hold_str}\n"
            f"_{self._session_line()}_"
        )
        self._post(msg)

    def notify_redemption(
        self,
        slug: str,
        shares: float,
        won: bool = True,
        entry_price: float = 0.0,
    ):
        """Notify when positions are redeemed after market resolution."""
        if not self._enabled:
            return

        asset, direction = _parse_slug(slug)
        market_label = f"{asset} {direction}" if asset != "?" else slug[:50]

        if won:
            pnl = shares * (1.0 - entry_price) if entry_price > 0 else 0.0
            cost = shares * entry_price if entry_price > 0 else 0.0
            self._wins += 1
            self._total_pnl += pnl
            icon = ":moneybag:"
            tag = "WIN"
            pnl_str = f"  |  +${pnl:.2f}" if pnl > 0 else ""
            cost_str = f"  |  risked ${cost:.2f}" if cost > 0 else ""
        else:
            pnl = -(shares * entry_price) if entry_price > 0 else 0.0
            cost = shares * entry_price if entry_price > 0 else 0.0
            self._losses += 1
            self._total_pnl += pnl
            icon = ":wastebasket:"
            tag = "LOSS"
            pnl_str = f"  |  -${abs(pnl):.2f}" if pnl < 0 else ""
            cost_str = f"  |  cost ${cost:.2f}" if cost > 0 else ""

        msg = (
            f"{icon} *REDEEMED ({tag})* [{self._instance}]\n"
            f"{market_label}  |  {shares:.0f} sh @ {entry_price:.3f}{pnl_str}{cost_str}\n"
            f"_{self._session_line()}_"
        )
        self._post(msg)

    def notify_startup(
        self,
        open_positions: int,
        balance: float = 0.0,
        dry_run: bool = False,
        active_assets: str = "",
        entry_mode: str = "",
        max_bet: float = 0.0,
        max_open_positions: int = 0,
        mid_price_stop_enabled: bool = False,
        mid_price_stop_pct: float = 0.0,
    ):
        """Notify on bot startup."""
        if not self._enabled:
            return

        if balance > 0:
            self._current_balance = balance
            if self._start_balance == 0:
                self._start_balance = balance

        bal_str = f"  |  ${balance:.2f}" if balance > 0 else ""
        mode_str = "  |  :warning: DRY RUN" if dry_run else "  |  :money_with_wings: LIVE"
        assets_str = f"\nAssets: {active_assets}" if active_assets else ""
        entry_str = f"  |  {entry_mode.upper()}" if entry_mode else ""

        config_parts = []
        if max_bet > 0:
            config_parts.append(f"MAX_BET=${max_bet:.0f}")
        if max_open_positions > 0:
            config_parts.append(f"MAX_POS={max_open_positions}")
        if mid_price_stop_enabled:
            config_parts.append(f"MID_STOP={mid_price_stop_pct:.0%}")
        else:
            config_parts.append("MID_STOP=off")
        config_line = f"\nConfig: {' | '.join(config_parts)}" if config_parts else ""

        msg = (
            f":rocket: *STARTED* [{self._instance}]{mode_str}\n"
            f"{open_positions} open positions{bal_str}{entry_str}"
            f"{assets_str}{config_line}"
        )
        self._post(msg)

    def notify_circuit_breaker(self, reason: str, cooldown_mins: int = 0):
        """Notify when a circuit breaker or cooldown is triggered."""
        if not self._enabled:
            return
        cooldown_str = f"  |  cooldown {cooldown_mins}m" if cooldown_mins > 0 else ""
        msg = (
            f":no_entry: *CIRCUIT BREAKER* [{self._instance}]\n"
            f"{reason}{cooldown_str}\n"
            f"_{self._session_line()}_"
        )
        self._post(msg)

    def notify_signal_blocked(
        self,
        asset: str,
        direction: str,
        reason: str,
        entry_price: float = 0.0,
    ):
        """Notify when a high-value signal is blocked by a filter (informational)."""
        if not self._enabled:
            return
        price_str = f" @ {entry_price:.3f}" if entry_price > 0 else ""
        msg = (
            f":hand: *SIGNAL BLOCKED* [{self._instance}]\n"
            f"*{asset} {direction}*{price_str}  |  {reason}"
        )
        self._post(msg)

    def notify_daily_summary(
        self,
        wins: int,
        losses: int,
        pnl: float,
        balance: float,
        top_win: float = 0.0,
        top_loss: float = 0.0,
        period: str = "24h",
    ):
        """Post a daily/periodic performance summary."""
        if not self._enabled:
            return
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        sign = "+" if pnl >= 0 else ""
        icon = ":chart_with_upwards_trend:" if pnl >= 0 else ":chart_with_downwards_trend:"

        top_win_str = f"  |  best +${top_win:.2f}" if top_win > 0 else ""
        top_loss_str = f"  |  worst -${abs(top_loss):.2f}" if top_loss < 0 else ""

        msg = (
            f"{icon} *{period.upper()} SUMMARY* [{self._instance}]\n"
            f"{wins}W {losses}L ({wr:.0f}%)  |  {sign}${pnl:.2f}{top_win_str}{top_loss_str}\n"
            f"Balance: ${balance:.2f}"
        )
        self._post(msg)

    def _post(self, text: str):
        """Send message in background thread. Never blocks, never raises."""
        t = threading.Thread(target=self._do_post, args=(text,), daemon=True)
        t.start()

    def _do_post(self, text: str):
        try:
            if self._mode == "bot":
                payload = json.dumps({
                    "channel": self._channel_id,
                    "text": text,
                }).encode("utf-8")
                req = Request(
                    "https://slack.com/api/chat.postMessage",
                    data=payload,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Authorization": f"Bearer {self._bot_token}",
                    },
                    method="POST",
                )
            else:
                payload = json.dumps({"text": text}).encode("utf-8")
                req = Request(
                    self._webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            urlopen(req, timeout=5)
        except Exception as e:
            logger.warning(f"Slack post failed: {e}")
