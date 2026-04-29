"""
Fire-and-forget notifications via ntfy.sh.
Enabled when NOTIFY_URL is set in .env (e.g., https://ntfy.sh/strategylab-john).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Module-level shared async client with a 5-second timeout
_client = httpx.AsyncClient(timeout=5.0)

# Cached result of reading NOTIFY_URL.
# False = not yet read; None = read, not set; str = read, has value.
_notify_url: Optional[str] = None
_notify_url_checked: bool = False


def _get_notify_url() -> Optional[str]:
    """Read NOTIFY_URL from environment, cache result. Returns None if not set."""
    global _notify_url, _notify_url_checked
    if not _notify_url_checked:
        _notify_url = os.environ.get("NOTIFY_URL") or None
        _notify_url_checked = True
    return _notify_url


async def notify(
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "",
) -> None:
    """Send a notification via ntfy.sh. Fire-and-forget — never raises."""
    url = _get_notify_url()
    if url is None:
        return
    try:
        headers = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = tags
        await _client.post(url, content=message.encode(), headers=headers)
    except Exception as exc:
        logger.warning("notify: failed to send notification: %s", exc)


async def notify_entry(
    symbol: str,
    direction: str,
    qty: float,
    price: float,
    strategy_name: str,
    bot_id: str,
) -> None:
    """Notify on successful trade entry."""
    side = direction.upper()
    tag = "chart_with_upwards_trend" if direction == "long" else "chart_with_downwards_trend"
    title = f"{side} entry: {symbol}"
    message = (
        f"{side} {qty} {symbol} @ ${price:.2f}\n"
        f"Strategy: {strategy_name}\n"
        f"Bot: {bot_id}"
    )
    await notify(title, message, priority="default", tags=tag)


async def notify_exit(
    symbol: str,
    direction: str,
    qty: float,
    price: float,
    pnl: float,
    reason: str,
    bot_id: str,
) -> None:
    """Notify on successful trade exit."""
    side = "COVER" if direction == "short" else "SELL"
    tag = "moneybag" if pnl > 0 else "money_with_wings"
    pnl_sign = "+" if pnl >= 0 else ""
    title = f"{side} exit: {symbol} ({pnl_sign}${pnl:.2f})"
    message = (
        f"{side} {qty} {symbol} @ ${price:.2f}\n"
        f"PnL: {pnl_sign}${pnl:.2f} | Reason: {reason}\n"
        f"Bot: {bot_id}"
    )
    await notify(title, message, priority="default", tags=tag)


async def notify_stop(
    symbol: str,
    direction: str,
    price: float,
    stop_type: str,
    bot_id: str,
) -> None:
    """Notify when a stop is triggered."""
    title = f"Stop triggered: {symbol}"
    message = (
        f"{stop_type.replace('_', ' ').title()} hit on {symbol} ({direction})\n"
        f"Price: ${price:.2f}\n"
        f"Bot: {bot_id}"
    )
    await notify(title, message, priority="high", tags="rotating_light")


async def notify_error(
    symbol: str,
    error_msg: str,
    bot_id: str,
) -> None:
    """Notify on bot error or pause."""
    title = f"Bot error: {symbol}"
    message = (
        f"Error on {symbol}: {error_msg}\n"
        f"Bot: {bot_id}"
    )
    await notify(title, message, priority="urgent", tags="warning")
