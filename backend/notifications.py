"""
Fire-and-forget notifications via ntfy.sh.
Enabled when NOTIFY_URL is set in .env (e.g., https://ntfy.sh/strategylab-john).
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Module-level shared async client with a 5-second timeout
_client = httpx.AsyncClient(timeout=5.0)

# Cached result of reading NOTIFY_URL.
# False = not yet read; None = read, not set; str = read, has value.
_notify_url: str | None = None
_notify_url_checked: bool = False


def _get_notify_url() -> str | None:
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
    priority: str = "default",
) -> None:
    """Notify on trade exit. Stop exits use priority='high' and a rotating_light tag."""
    side = "COVER" if direction == "short" else "SELL"
    is_stop = reason in ("stop_loss", "trailing_stop")
    tag = "rotating_light" if is_stop else ("moneybag" if pnl > 0 else "money_with_wings")
    effective_priority = "high" if is_stop else priority
    pnl_sign = "+" if pnl >= 0 else ""
    title = f"{side} exit: {symbol} ({pnl_sign}${pnl:.2f})"
    message = (
        f"{side} {qty} {symbol} @ ${price:.2f}\n"
        f"PnL: {pnl_sign}${pnl:.2f} | Reason: {reason}\n"
        f"Bot: {bot_id}"
    )
    await notify(title, message, priority=effective_priority, tags=tag)


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


async def notify_test(
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "",
) -> None:
    """Send a test notification. Unlike notify(), re-raises on failure so the caller can detect it."""
    url = _get_notify_url()
    if url is None:
        return
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        await _client.post(url, content=message.encode(), headers=headers)
    except Exception as exc:
        logger.warning("notify_test: failed to send notification: %s", exc)
        raise


async def close_client() -> None:
    """Close the shared httpx client. Call from FastAPI lifespan on shutdown."""
    await _client.aclose()
