"""
routes/notifications.py — Notification management endpoints.

Endpoints:
  GET  /api/notifications/test   — send a test notification and report status
  GET  /api/notifications/status — check whether notifications are configured
"""

from fastapi import APIRouter

from notifications import _get_notify_url, notify

router = APIRouter(prefix="/api/notifications")


@router.get("/status")
async def get_notification_status():
    """Return whether push notifications are configured."""
    url = _get_notify_url()
    return {
        "enabled": url is not None,
        "url": url if url else None,
    }


@router.get("/test")
async def send_test_notification():
    """Send a test notification via ntfy.sh. Returns whether it succeeded."""
    url = _get_notify_url()
    if url is None:
        return {
            "sent": False,
            "reason": "NOTIFY_URL is not set in .env — add it and restart the server",
        }
    try:
        await notify(
            title="StrategyLab test",
            message="Notifications are working! Your bot alerts are active.",
            priority="default",
            tags="white_check_mark",
        )
        return {"sent": True, "url": url}
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}
