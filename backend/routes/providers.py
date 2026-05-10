import os
import pathlib
import shutil
import tempfile
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_available_brokers, get_active_broker, set_active_broker, get_rate_counter
from broker_health_singleton import get_monitor

router = APIRouter()

# F70: serialize read-modify-write on .env so concurrent _persist_env calls for
# different keys can't race and clobber each other at the os.replace step.
_env_lock = threading.Lock()


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


def _persist_env(key: str, value: str, env_path: Optional[pathlib.Path] = None):
    """Write a key=value to backend/.env so it survives restarts.

    The lock serializes in-process callers; cross-process writers (a second
    gunicorn worker, a deployment script editing .env) are not protected and
    will lose the race on whichever process called os.replace second.
    """
    if env_path is None:
        env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    with _env_lock:
        # F76: avoid the exists()→read_text() TOCTOU. A concurrent unlink between
        # the two calls would raise FileNotFoundError on read_text; treat it the
        # same as "no file" rather than crashing the request.
        try:
            lines = env_path.read_text().splitlines()
            existed = True
        except FileNotFoundError:
            lines = []
            existed = False
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
            fd.flush()
            os.fsync(fd.fileno())
            fd.close()
            # Only copy mode when the source file existed at read time. Avoids
            # masking a real failure (e.g. chmod on the temp file) under the
            # same FileNotFoundError as the legitimate "first-time write" case.
            if existed:
                try:
                    shutil.copymode(str(env_path), fd.name)
                except FileNotFoundError:
                    # External unlink between read_text and copymode — proceed
                    # with the temp file's default mode rather than aborting.
                    pass
            os.replace(fd.name, str(env_path))
        except Exception:
            try:
                fd.close()
            except Exception:
                pass
            try:
                os.unlink(fd.name)
            except OSError:
                pass
            raise
