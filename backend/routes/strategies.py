"""Saved-strategies persistence endpoints.

Provides server-side storage for saved strategies, mirroring the browser
localStorage key 'strategylab-saved-strategies'. The frontend is the sole
writer of strategy content; server-side validation covers only the top-level
shape (list of objects, each with a valid name string).

Endpoints:
    GET  /strategies              → list[dict]
    PUT  /strategies              → list[dict]  (bulk replace)
    DELETE /strategies/{name}     → list[dict]  (remove by name)
    POST /strategies/seed         → {seeded: bool, ...}  (idempotent init)
"""

import json
import logging
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Any

from fileutil import atomic_write_text
from journal import DATA_DIR

logger = logging.getLogger(__name__)

STRATEGIES_PATH = DATA_DIR / "saved_strategies.json"

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_strategies() -> list[dict]:
    """Read saved_strategies.json, returning [] on missing or corrupt file."""
    if not STRATEGIES_PATH.exists():
        return []
    try:
        data = json.loads(STRATEGIES_PATH.read_text())
        if not isinstance(data, list):
            logger.warning("saved_strategies.json: unexpected top-level type %s; returning []", type(data).__name__)
            return []
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("saved_strategies.json is unparseable (%s); returning []", exc)
        return []


def _write_strategies(strategies: list[dict]) -> None:
    atomic_write_text(STRATEGIES_PATH, json.dumps(strategies, indent=2))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class StrategyItem(BaseModel):
    """Top-level validation for a single saved strategy.

    Deep validation of nested rule fields is intentionally omitted — the
    frontend is the authoritative writer and round-trips Pydantic-validated
    Rule objects when running backtests.
    """
    name: str = Field(..., min_length=1, max_length=120)

    model_config = {"extra": "allow"}


class StrategiesPayload(BaseModel):
    strategies: list[StrategyItem] = Field(..., max_length=200)

    @field_validator('strategies', mode='before')
    @classmethod
    def _validate_items_are_objects(cls, v):
        if not isinstance(v, list):
            return v
        for i, item in enumerate(v):
            if not isinstance(item, dict):
                raise ValueError(f"item at index {i} must be a JSON object, got {type(item).__name__}")
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/strategies")
def get_strategies() -> list[dict]:
    return _read_strategies()


@router.put("/strategies")
def put_strategies(payload: list[Any]) -> list[dict]:
    """Bulk replace all saved strategies.

    Accepts a JSON array directly (not wrapped in an object). Each element
    must be a dict with a 'name' string (1–120 chars). Max 200 items.
    """
    if len(payload) > 200:
        raise HTTPException(status_code=422, detail=f"too many strategies (max 200, got {len(payload)})")
    validated: list[dict] = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"item at index {i} must be a JSON object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=422, detail=f"item at index {i}: 'name' must be a non-empty string")
        if len(name) > 120:
            raise HTTPException(status_code=422, detail=f"item at index {i}: 'name' too long (max 120 chars)")
        validated.append(item)
    _write_strategies(validated)
    return validated


@router.delete("/strategies/{name}")
def delete_strategy(name: str) -> list[dict]:
    """Remove the strategy with the given name (URL-decoded).

    Returns 404 if not found, 200 with the updated list otherwise.
    """
    decoded_name = unquote(name)
    strategies = _read_strategies()
    new_list = [s for s in strategies if s.get("name") != decoded_name]
    if len(new_list) == len(strategies):
        raise HTTPException(status_code=404, detail=f"strategy not found: {decoded_name!r}")
    _write_strategies(new_list)
    return new_list


@router.post("/strategies/seed")
def seed_strategies(payload: list[Any]) -> dict:
    """Idempotent first-time sync from browser localStorage.

    Only writes if the on-disk file is currently empty or missing.
    Returns {seeded: true} if written, {seeded: false, reason: "already_populated"} otherwise.
    """
    existing = _read_strategies()
    if existing:
        return {"seeded": False, "reason": "already_populated"}
    # Reuse the PUT validation path
    if len(payload) > 200:
        raise HTTPException(status_code=422, detail=f"too many strategies (max 200, got {len(payload)})")
    validated: list[dict] = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"item at index {i} must be a JSON object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=422, detail=f"item at index {i}: 'name' must be a non-empty string")
        if len(name) > 120:
            raise HTTPException(status_code=422, detail=f"item at index {i}: 'name' too long (max 120 chars)")
        validated.append(item)
    _write_strategies(validated)
    return {"seeded": True}
