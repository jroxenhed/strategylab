import os
import pathlib
import tempfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_available_brokers, get_active_broker, set_active_broker, get_rate_counter
from broker_health_singleton import get_monitor

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}


def _get_data_rpm() -> int:
    try:
        from shared import get_data_rate_counter
        return get_data_rate_counter().calls_per_minute()
    except Exception:
        return 0


def _broker_payload():
    from bot_runner import get_poll_ms
    monitor = get_monitor()
    health = monitor.all_health() if monitor else {}
    warmup = monitor.is_warming_up() if monitor else False
    return {
        "active": get_active_broker(),
        "available": get_available_brokers(),
        "health": health,
        "heartbeat_warmup": warmup,
        "poll_interval_ms": get_poll_ms() or None,
        "api_calls_per_minute": get_rate_counter().calls_per_minute(),
        "data_calls_per_minute": _get_data_rpm(),
    }


@router.get("/api/broker")
def get_broker():
    return _broker_payload()


class SetBrokerRequest(BaseModel):
    broker: str


@router.put("/api/broker")
def set_broker(req: SetBrokerRequest):
    set_active_broker(req.broker)
    return _broker_payload()


@router.patch("/api/broker/poll-interval")
def set_poll_interval(body: dict):
    ms = body.get("ms")
    if not isinstance(ms, int) or ms < 10 or ms > 60000:
        raise HTTPException(400, "ms must be integer between 10 and 60000")
    from bot_runner import set_poll_ms
    set_poll_ms(ms)
    _persist_env("BOT_POLL_MS", str(ms))
    return {"poll_interval_ms": ms}


def _persist_env(key: str, value: str):
    """Write a key=value to backend/.env so it survives restarts."""
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    content = "\n".join(lines) + "\n"
    fd = tempfile.NamedTemporaryFile(
        mode='w', dir=str(env_path.parent), suffix='.tmp', delete=False
    )
    try:
        fd.write(content)
        fd.close()
        os.replace(fd.name, str(env_path))
    except Exception:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise
