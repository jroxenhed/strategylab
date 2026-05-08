from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_available_brokers, get_active_broker, set_active_broker, get_rate_counter
from broker_health_singleton import get_monitor

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}


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
    if not isinstance(ms, int) or ms < 100 or ms > 60000:
        raise HTTPException(400, "ms must be integer between 100 and 60000")
    from bot_runner import set_poll_ms
    set_poll_ms(ms)
    return {"poll_interval_ms": ms}
