"""Authenticated Google Shopping access with SerpAPI credentials kept in FastAPI."""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.core.auth import get_current_user
from app.database.models import UserModel

router = APIRouter(prefix="/market", tags=["Market intelligence"])
logger = logging.getLogger(__name__)
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"
PUBLIC_FIELDS = (
    "product_id", "title", "source", "extracted_price", "price",
    "link", "product_link", "extracted_old_price", "old_price",
    "rating", "reviews", "shipping", "delivery", "thumbnail",
)


class ShoppingQuery(BaseModel):
    query: str = Field(min_length=2, max_length=180)


@router.get("/providers")
def providers(_user: UserModel = Depends(get_current_user)):
    key = settings.serpapi_key
    return {"googleShopping": bool(key and key.get_secret_value().strip())}


@router.post("/google-shopping")
async def google_shopping(
    body: ShoppingQuery,
    _user: UserModel = Depends(get_current_user),
):
    key = settings.serpapi_key
    api_key = key.get_secret_value().strip() if key else ""
    if not api_key:
        raise HTTPException(status_code=503, detail="SerpAPI não configurada no backend.")

    query = " ".join(body.query.split())
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="Termo de busca inválido.")

    # Only the FastAPI process contacts SerpAPI; never return the key, URL
    # with credentials, raw search metadata, or upstream exception details.
    params = {
        "engine": "google_shopping",
        "q": query,
        "gl": "br",
        "hl": "pt-br",
        "api_key": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.get(SERPAPI_SEARCH_URL, params=params)
    except (httpx.RequestError, httpx.TimeoutException):
        logger.warning("serpapi_request_unavailable")
        raise HTTPException(status_code=502, detail="Não foi possível consultar o Google Shopping.")

    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="Limite de consultas à SerpAPI atingido.")
    if response.status_code != 200:
        logger.warning("serpapi_bad_status status=%s", response.status_code)
        raise HTTPException(status_code=502, detail="Google Shopping indisponível.")

    if len(response.content) > 2_000_000:
        raise HTTPException(status_code=502, detail="Resposta do Google Shopping muito grande.")
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Resposta inválida do Google Shopping.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Resposta inválida do Google Shopping.")

    direct = data.get("shopping_results")
    categorized = data.get("categorized_shopping_results")
    listings = list(direct) if isinstance(direct, list) else []
    if isinstance(categorized, list):
        for category in categorized[:12]:
            if isinstance(category, dict) and isinstance(category.get("shopping_results"), list):
                listings.extend(category["shopping_results"][:30])

    if data.get("error") and not listings:
        status = data.get("search_metadata")
        if not (isinstance(status, dict) and status.get("status") == "Success"):
            raise HTTPException(status_code=502, detail="SerpAPI não conseguiu concluir a pesquisa.")

    sanitized = []
    for item in listings:
        if not isinstance(item, dict):
            continue
        sanitized.append({
            name: item[name]
            for name in PUBLIC_FIELDS
            if name in item and (
                item[name] is None or type(item[name]) in (str, int, float, bool)
            )
        })
        if len(sanitized) >= 60:
            break
    return {"shopping_results": sanitized}
