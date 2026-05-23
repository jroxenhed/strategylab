"""API request/response models for nodebuilder routes.

Unit 3: AutoRenderResponse
Unit 8b: GraphBacktestRequest (stub — populated later)
"""
from __future__ import annotations

from pydantic import BaseModel

from nodebuilder.models import Graph


class AutoRenderResponse(BaseModel):
    """Response from POST /api/nodebuilder/auto_render."""
    graph: Graph
