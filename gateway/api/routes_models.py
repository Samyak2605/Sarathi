from __future__ import annotations

from fastapi import APIRouter, Depends

from gateway.auth.dependency import authenticate
from gateway.metering.pricing import PRICING
from gateway.schemas import ModelInfo, ModelList

router = APIRouter()


@router.get("/v1/models", response_model=ModelList)
async def list_models(_=Depends(authenticate)) -> ModelList:
    return ModelList(
        data=[ModelInfo(id=model_id, tier=price.tier) for model_id, price in PRICING.items()]
        + [ModelInfo(id="auto", tier=None)]
    )
