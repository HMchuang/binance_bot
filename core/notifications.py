"""
Fire-and-forget Discord + Telegram notifications for trade events.
All sends run in a ThreadPoolExecutor — the trading loop is never blocked.
Disabled gracefully if both discord_webhook and telegram_token are None.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import requests

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.config import TradingConfig

log = get_logger("notifications")


@dataclass
class NotificationEvent:
    event_type: str   # "buy","sell","stop_loss","take_profit","trailing_stop","error","started","stopped"
    symbol:     str
    side:       str   # "BUY" or "SELL"
    price:      float
    qty:        float
    pnl_usd:    Optional[float] = None
    pnl_pct:    Optional[float] = None
    reason:     str = ""
    timestamp:  datetime = field(default_factory=datetime.now)
    mode:       str = "sim"


class NotificationManager:
    def __init__(self, config: "TradingConfig"):
        self.config   = config
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notif")
        self._enabled  = bool(config.discord_webhook or config.telegram_token)

    def send(self, event: NotificationEvent) -> None:
        if not self._enabled:
            return
        if self.config.discord_webhook:
            self._executor.submit(self._send_discord, event)
        if self.config.telegram_token and self.config.telegram_chat_id:
            self._executor.submit(self._send_telegram, event)

    def update_config(self, config: "TradingConfig") -> None:
        self.config   = config
        self._enabled = bool(config.discord_webhook or config.telegram_token)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    # ── Discord ──────────────────────────────────────────────────────────────

    def _send_discord(self, event: NotificationEvent) -> None:
        try:
            embed = self._format_discord_embed(event)
            r = requests.post(
                self.config.discord_webhook,
                json={"embeds": [embed]},
                timeout=5,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Discord notification failed: {e}")

    def _format_discord_embed(self, event: NotificationEvent) -> dict:
        is_buy    = event.side == "BUY"
        is_profit = event.pnl_usd is not None and event.pnl_usd > 0
        if is_buy:
            color = 0x3B82F6; title = f"BUY {event.symbol}"
        elif is_profit:
            color = 0x22C55E; title = f"SELL {event.symbol} — PROFIT"
        else:
            color = 0xEF4444; title = f"SELL {event.symbol} — LOSS"

        fields = [
            {"name": "Price",  "value": f"`${event.price:,.4f}`",  "inline": True},
            {"name": "Qty",    "value": f"`{event.qty:.6f}`",        "inline": True},
            {"name": "Mode",   "value": event.mode.upper(),           "inline": True},
        ]
        if event.pnl_usd is not None:
            sign = "+" if event.pnl_usd >= 0 else ""
            fields.append({
                "name": "P&L",
                "value": f"`{sign}${event.pnl_usd:.2f}` ({sign}{event.pnl_pct:.2f}%)",
                "inline": True,
            })
        if event.reason:
            fields.append({"name": "Reason", "value": event.reason, "inline": False})
        return {
            "title":  title,
            "color":  color,
            "fields": fields,
            "footer": {"text": f"Binance Bot  •  {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"},
        }

    # ── Telegram ─────────────────────────────────────────────────────────────

    def _send_telegram(self, event: NotificationEvent) -> None:
        try:
            text = self._format_telegram_message(event)
            url  = f"https://api.telegram.org/bot{self.config.telegram_token}/sendMessage"
            r    = requests.post(
                url,
                json={"chat_id": self.config.telegram_chat_id,
                      "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Telegram notification failed: {e}")

    def _format_telegram_message(self, event: NotificationEvent) -> str:
        is_buy = event.side == "BUY"
        emoji  = "🔵" if is_buy else ("🟢" if (event.pnl_usd or 0) >= 0 else "🔴")
        lines = [
            f"{emoji} <b>{event.side} {event.symbol}</b>",
            f"Price: <code>${event.price:,.4f}</code>",
            f"Qty: <code>{event.qty:.6f}</code>",
            f"Mode: {event.mode.upper()}",
        ]
        if event.pnl_usd is not None:
            sign = "+" if event.pnl_usd >= 0 else ""
            lines.append(
                f"P&amp;L: <b>{sign}${event.pnl_usd:.2f} ({sign}{event.pnl_pct:.2f}%)</b>")
        if event.reason:
            lines.append(f"Reason: {event.reason}")
        lines.append(f"<i>{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</i>")
        return "\n".join(lines)
