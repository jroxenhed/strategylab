from fastapi import APIRouter
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_available_brokers, get_active_broker, set_active_broker
from broker_health_singleton import get_monitor

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}


def _broker_payload():
    monitor = get_monitor()
    health = monitor.all_health() if monitor else {}
    warmup = monitor.is_warming_up() if monitor else False
    return {
        "active": get_active_broker(),
        "available": get_available_brokers(),
        "health": health,
        "heartbeat_warmup": warmup,
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
