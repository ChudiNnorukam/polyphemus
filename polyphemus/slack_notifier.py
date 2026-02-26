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
        if self._enabled:
            logger.info(f"Slack notifier enabled: mode={self._mode}, instance={instance_name}")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def notify_entry(
        self,
        slug: str,
        asset: str,
        direction: str,
        entry_price: float,
        size_usd: float,
        shares: float,
        momentum_pct: float = 0.0,
    ):
        if not self._enabled:
            return
        payout = shares * (1.0 - entry_price)
        msg = (
            f":chart_with_upwards_trend: *ENTRY* [{self._instance}]\n"
            f"*{asset} {direction}* @ ${entry_price:.3f}  |  ${size_usd:.2f} ({shares:.0f} shares)\n"
            f"Momentum: {momentum_pct:+.2%}  |  Max payout: +${payout:.2f}\n"
            f"`{slug}`"
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
    ):
        if not self._enabled:
            return
        is_win = pnl > 0
        if is_win:
            self._wins += 1
        else:
            self._losses += 1
        self._total_pnl += pnl

        total = self._wins + self._losses
        wr = (self._wins / total * 100) if total > 0 else 0

        icon = ":white_check_mark:" if is_win else ":x:"
        sign = "+" if pnl >= 0 else ""
        hold_str = f"{hold_secs:.0f}s" if hold_secs > 0 else "n/a"

        msg = (
            f"{icon} *{'WIN' if is_win else 'LOSS'}* [{self._instance}]\n"
            f"*{asset} {direction}*  |  ${entry_price:.3f} -> ${exit_price:.3f}  |  {sign}${pnl:.2f}\n"
            f"Exit: {exit_reason}  |  Hold: {hold_str}\n"
            f"Session: {self._wins}W/{self._losses}L ({wr:.0f}% WR)  |  PnL: {sign if self._total_pnl >= 0 else ''}${self._total_pnl:.2f}\n"
            f"`{slug}`"
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
