"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b
POST /api/nodebuilder/validate     — Unit 8b
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/nodebuilder", tags=["nodebuilder"])
