"""Telegram approval gate for weather signals.

Flow:
  1. submit(signal)        -- fire-and-forget, sends Telegram message with YES/NO buttons
  2. start()               -- background polling loop, receives callback queries
  3. _handle_approve()     -- calls on_execute(signal) when user taps YES
  4. _handle_reject()      -- discards signal on NO or timeout

Config required (in .env):
  TELEGRAM_BOT_TOKEN=<from @BotFather>
  TELEGRAM_CHAT_ID=<your numeric chat ID>
  TELEGRAM_APPROVAL_TIMEOUT_SECS=300  (optional, default 5 min)
"""

import asyncio
import time
import uuid
from typing import Callable, Dict, Optional

import aiohttp

from .config import Settings, setup_logger

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramApprover:
    def __init__(self, config: Settings, on_execute: Callable):
        self._token = config.telegram_bot_token
        self._chat_id = str(config.telegram_chat_id)
        self._timeout = config.telegram_approval_timeout_secs
        self._on_execute = on_execute
        self._logger = setup_logger("polyphemus.telegram")
        # approval_id -> {signal, expires, msg_id}
        self._pending: Dict[str, dict] = {}
        self._offset = 0
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id and self._chat_id != "0")

    # =========================================================================
    # Public API
    # =========================================================================

    async def submit(self, signal: dict) -> None:
        """Fire-and-forget: queue signal for Telegram approval. Does not block."""
        if not self.enabled:
            self._logger.warning("Telegram not configured — signal discarded")
            return
        approval_id = uuid.uuid4().hex[:8]
        self._pending[approval_id] = {
            "signal": signal,
            "expires": time.time() + self._timeout,
            "msg_id": None,
        }
        try:
            await self._send_approval_message(signal, approval_id)
        except Exception as e:
            self._logger.error(f"Telegram send failed: {e}")
            self._pending.pop(approval_id, None)

    async def start(self) -> None:
        """Background polling loop. Runs forever alongside the main bot."""
        if not self.enabled:
            self._logger.info("Telegram approver disabled (no token/chat_id set)")
            return
        self._logger.info(
            f"Telegram approver started | chat_id={self._chat_id} | "
            f"timeout={self._timeout}s"
        )
        async with aiohttp.ClientSession() as self._session:
            while True:
                try:
                    await self._poll_once()
                    self._expire_pending()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._logger.debug(f"Telegram poll error: {e}")
                await asyncio.sleep(1)

    # =========================================================================
    # Internal
    # =========================================================================

    async def _api(self, method: str, **kwargs) -> dict:
        if not self._session:
            return {}
        url = TELEGRAM_API.format(token=self._token, method=method)
        async with self._session.post(
            url, json=kwargs, timeout=aiohttp.ClientTimeout(total=35)
        ) as resp:
            return await resp.json()

    async def _poll_once(self) -> None:
        data = await self._api(
            "getUpdates",
            offset=self._offset,
            timeout=30,
            allowed_updates=["callback_query"],
        )
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if cb:
                await self._handle_callback(cb)

    async def _handle_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        cb_id = cb["id"]
        if not data.startswith("weather_"):
            return

        parts = data.split("_", 2)  # weather_approve_<id> or weather_reject_<id>
        if len(parts) != 3:
            return
        _, action, approval_id = parts

        pending = self._pending.get(approval_id)
        if not pending:
            await self._api("answerCallbackQuery", callback_query_id=cb_id, text="Expired.")
            return

        if action == "approve":
            await self._api("answerCallbackQuery", callback_query_id=cb_id, text="Approved ✅")
            await self._edit_message(approval_id, "✅ *Approved — executing order...*")
            self._pending.pop(approval_id, None)
            try:
                await self._on_execute(pending["signal"])
            except Exception as e:
                self._logger.error(f"Telegram-approved execution failed: {e}")
                await self._notify(f"⚠️ Execution failed: {e}")
        elif action == "reject":
            await self._api("answerCallbackQuery", callback_query_id=cb_id, text="Rejected ❌")
            await self._edit_message(approval_id, "❌ *Rejected — signal discarded.*")
            self._pending.pop(approval_id, None)
            self._logger.info(
                f"Signal rejected via Telegram: "
                f"{pending['signal'].get('slug', '?')}"
            )

    def _expire_pending(self) -> None:
        now = time.time()
        expired = [k for k, v in self._pending.items() if v["expires"] < now]
        for approval_id in expired:
            pending = self._pending.pop(approval_id)
            slug = pending["signal"].get("slug", "?")
            self._logger.info(f"Telegram approval timed out: {slug}")
            asyncio.create_task(
                self._edit_message(approval_id, "⏰ *Timed out — signal discarded.*")
            )

    async def _send_approval_message(self, signal: dict, approval_id: str) -> None:
        city = signal.get("weather_city", "?")
        question = signal.get("market_title", "")[:70]
        outcome = signal.get("outcome", "Yes")
        noaa = signal.get("noaa_prob", 0.0)
        edge = signal.get("edge", 0.0)
        price = signal.get("price", 0.0)
        hours = signal.get("time_remaining_secs", 0) / 3600
        meta = signal.get("metadata", {})
        bet_type = meta.get("bet_type", "?")
        members_str = meta.get("members_str", "")
        mean_f = meta.get("ensemble_mean_f")
        center_f = meta.get("bucket_center_f")

        # ECMWF delta line
        if mean_f is not None and center_f is not None:
            delta = mean_f - center_f
            sign = "+" if delta >= 0 else ""
            forecast_line = (
                f"📡 ECMWF: {mean_f:.1f}°F vs bucket {center_f:.1f}°F "
                f"({sign}{delta:.1f}°F)"
            )
        elif mean_f is not None:
            forecast_line = f"📡 ECMWF forecast: {mean_f:.1f}°F"
        else:
            forecast_line = ""

        noaa_line = f"🎯 {bet_type} — {noaa:.1%}"
        if members_str:
            noaa_line += f" ({members_str} members)"

        text = (
            f"🌤 *Weather Signal — Approve?*\n\n"
            f"📍 *{city}*\n"
            f"🎲 *Buy {outcome}*\n"
            f"_{question}_\n\n"
            f"{forecast_line}\n"
            f"{noaa_line}\n"
            f"💰 Ask: ${price:.3f} | Edge: {edge:+.1%}\n"
            f"💵 Cost: ~${self._timeout and 2.0:.2f} | ⏱ {hours:.1f}h left\n\n"
            f"_Expires in {self._timeout // 60} min_"
        )

        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"weather_approve_{approval_id}"},
                {"text": "❌ Reject",  "callback_data": f"weather_reject_{approval_id}"},
            ]]
        }

        resp = await self._api(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        if resp.get("ok") and resp.get("result"):
            if approval_id in self._pending:
                self._pending[approval_id]["msg_id"] = resp["result"]["message_id"]

    async def _edit_message(self, approval_id: str, text: str) -> None:
        pending = self._pending.get(approval_id)
        msg_id = pending["msg_id"] if pending else None
        if not msg_id:
            return
        try:
            await self._api(
                "editMessageText",
                chat_id=self._chat_id,
                message_id=msg_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception:
            pass

    async def _notify(self, text: str) -> None:
        try:
            await self._api(
                "sendMessage",
                chat_id=self._chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception:
            pass
