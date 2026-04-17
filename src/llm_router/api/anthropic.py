from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.services.gateway import handle_proxy_request


router = APIRouter(tags=["anthropic"])


def _extract_api_key(x_api_key: str | None) -> str:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing x-api-key header")
    return x_api_key.strip()


@router.post("/v1/messages")
async def messages(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    session: AsyncSession = request.state.db
    payload = await request.json()
    raw_api_key = _extract_api_key(x_api_key)
    return await handle_proxy_request(
        session,
        protocol=ProviderProtocol.ANTHROPIC,
        payload=payload,
        raw_api_key=raw_api_key,
        headers=dict(request.headers),
    )
