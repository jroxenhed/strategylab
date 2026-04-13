from fastapi import APIRouter
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_available_brokers, get_active_broker, set_active_broker

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}


@router.get("/api/broker")
def get_broker():
    return {
        "active": get_active_broker(),
        "available": get_available_brokers(),
    }


class SetBrokerRequest(BaseModel):
    broker: str


@router.put("/api/broker")
def set_broker(req: SetBrokerRequest):
    set_active_broker(req.broker)
    return {
        "active": get_active_broker(),
        "available": get_available_brokers(),
    }
