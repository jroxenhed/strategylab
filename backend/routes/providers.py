from fastapi import APIRouter
from shared import get_available_providers

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}
