from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.services.gateway import handle_proxy_request


router = APIRouter(prefix="/anthropic", tags=["anthropic"])


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    return authorization.split(" ", 1)[1].strip()


@router.get("/v1/models")
async def list_models(request: Request, authorization: str | None = Header(default=None)):
    from sqlalchemy import select

    from llm_router.domain.models import ApiKey, LogicalModel
    from llm_router.core.security import hash_api_key

    session: AsyncSession = request.state.db
    raw_api_key = _extract_bearer_token(authorization)
    api_key = (
        await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_api_key), ApiKey.status == "active"))
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    stmt = select(LogicalModel).where(LogicalModel.is_active)
    if api_key.allowed_logical_models_json:
        stmt = stmt.where(LogicalModel.name.in_(api_key.allowed_logical_models_json))
    items = (await session.execute(stmt)).scalars().all()
    data = [
        {
            "type": "model",
            "id": item.name,
            "display_name": item.description or item.name,
            "created_at": item.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for item in items
    ]
    return {
        "data": data,
        "has_more": False,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }


@router.get("/v1/models/{model_id}")
async def get_model(model_id: str, request: Request, authorization: str | None = Header(default=None)):
    from sqlalchemy import select

    from llm_router.domain.models import ApiKey, LogicalModel
    from llm_router.core.security import hash_api_key

    session: AsyncSession = request.state.db
    raw_api_key = _extract_bearer_token(authorization)
    api_key = (
        await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_api_key), ApiKey.status == "active"))
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if api_key.allowed_logical_models_json and model_id not in api_key.allowed_logical_models_json:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    model = (
        await session.execute(select(LogicalModel).where(LogicalModel.name == model_id, LogicalModel.is_active))
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return {
        "type": "model",
        "id": model.name,
        "display_name": model.description or model.name,
        "created_at": model.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.post("/v1/messages")
async def messages(
    request: Request,
    authorization: str | None = Header(default=None),
):
    session: AsyncSession = request.state.db
    payload = await request.json()
    raw_api_key = _extract_bearer_token(authorization)
    return await handle_proxy_request(
        session,
        protocol=ProviderProtocol.ANTHROPIC,
        payload=payload,
        raw_api_key=raw_api_key,
        headers=dict(request.headers),
        request_path="/v1/messages",
        request=request,
    )
