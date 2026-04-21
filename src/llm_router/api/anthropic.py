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
    )
