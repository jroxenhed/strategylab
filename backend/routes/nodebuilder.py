"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b
POST /api/nodebuilder/validate     — Unit 8b
"""
from fastapi import APIRouter

from models import StrategyRequest
from nodebuilder.api_models import AutoRenderResponse
from nodebuilder.from_rules import auto_render

router = APIRouter(prefix="/api/nodebuilder", tags=["nodebuilder"])


@router.post("/auto_render", response_model=AutoRenderResponse, response_model_by_alias=True)
def post_auto_render(req: StrategyRequest) -> AutoRenderResponse:
    """Translate a StrategyRequest into a read-only Graph for the T1 viewer."""
    graph = auto_render(req)
    return AutoRenderResponse(graph=graph)
