from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.services.gateway import handle_proxy_request


router = APIRouter(prefix="/v1", tags=["openai"])


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    return authorization.split(" ", 1)[1].strip()


@router.get("/models")
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
    return {"object": "list", "data": [{"id": item.name, "object": "model"} for item in items]}


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
):
    session: AsyncSession = request.state.db
    payload = await request.json()
    raw_api_key = _extract_bearer_token(authorization)
    route_path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    return await handle_proxy_request(
        session,
        protocol=ProviderProtocol.OPENAI,
        payload=payload,
        raw_api_key=raw_api_key,
        headers=dict(request.headers),
        request_path="/chat/completions",
    )
