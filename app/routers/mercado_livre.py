"""Per-tenant Mercado Livre OAuth and read-only seller endpoints."""
from datetime import datetime, timezone, timedelta
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
from app.core.auth import get_current_user
from app.core.oauth_state import consume_state, issue_state
from app.core.security import encrypt_token
from app.config.settings import settings
from app.connectors.mercado_livre.identity import normalized_seller_identity
from app.database.session import get_db
from app.database.models import (
    UserModel, MercadoLivreConnectionModel, MercadoLivreOAuthStateModel,
    CompanyDataSourceModel,
)
from app.connectors.mercado_livre.auth import (
    API_BASE_URL, MercadoLivreOAuthError, build_authorization_url,
    exchange_authorization_code, fetch_profile, get_valid_access_token,
    token_expiration,
)

router = APIRouter(prefix="/connectors/mercado-livre", tags=["Mercado Livre"])
logger = logging.getLogger(__name__)


def get_account(db: Session, user_id: int, account_id: int | None):
    query = select(MercadoLivreConnectionModel).where(
        MercadoLivreConnectionModel.user_id == user_id)
    if account_id is not None:
        query = query.where(MercadoLivreConnectionModel.mercado_livre_user_id == account_id)
    connections = db.scalars(query.order_by(MercadoLivreConnectionModel.id).limit(2)).all()
    if not connections:
        raise HTTPException(404, "Nenhuma conta Mercado Livre vinculada.")
    if len(connections) > 1:
        raise HTTPException(400, "Informe account_id para selecionar a conta.")
    return connections[0]


def _resource_category(path: str) -> str:
    """Nome fixo para observabilidade; nunca exponha IDs, query ou tokens."""
    if path == "/users/me":
        return "profile"
    if path.startswith("/orders/"):
        return "orders"
    if path.startswith("/items/"):
        return "item_details"
    if path.endswith("/items/search"):
        return "item_search"
    if "visits" in path:
        return "visits"
    return "other"


async def seller_request(db: Session, connection: MercadoLivreConnectionModel,
                         path: str, params: dict | None = None):
    category = _resource_category(path)
    try:
        token = await get_valid_access_token(connection, db)
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                f"{API_BASE_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as error:
        # Status HTTP e categoria bastam para descobrir 401, 403 e 429.
        # Nunca registramos URLs, cabeçalhos ou corpo da resposta.
        logger.warning(
            "[mercado-livre-data] resource=%s upstream_status=%s",
            category, error.response.status_code,
        )
    except MercadoLivreOAuthError as error:
        logger.warning(
            "[mercado-livre-data] resource=%s oauth_phase=%s upstream_status=%s provider_error=%s",
            category, error.phase, error.http_status or "unknown", error.provider_error,
        )
    except (httpx.RequestError, RuntimeError, ValueError, TypeError):
        logger.warning("[mercado-livre-data] resource=%s upstream_status=unavailable", category)
    raise HTTPException(502, "Não foi possível consultar o Mercado Livre.")


# POST é usado pela UI SaaS com proteção CSRF no Next.js.
# GET permanece para o ativador CLI já publicado.
@router.post("/authorize")
@router.get("/authorize")
def authorize(user: UserModel = Depends(get_current_user),
              db: Session = Depends(get_db)):
    try:
        state = issue_state(db, MercadoLivreOAuthStateModel, user.id)
        return {"authorization_url": build_authorization_url(state),
                "expires_in": 600}
    except RuntimeError:
        raise HTTPException(503, "Credenciais Mercado Livre não configuradas.")


@router.get("/callback")
async def callback(state: str = Query(min_length=16, max_length=200),
                   code: str | None = Query(default=None, max_length=2048),
                   error: str | None = Query(default=None, max_length=200),
                   db: Session = Depends(get_db)):
    user_id = consume_state(db, MercadoLivreOAuthStateModel, state)
    if error or not code:
        raise HTTPException(400, "Autorização Mercado Livre não concluída.")
    try:
        tokens = await exchange_authorization_code(code, state)
        profile = await fetch_profile(str(tokens["access_token"]))
        seller_id = int(tokens["user_id"])
        if seller_id != int(profile["id"]):
            raise MercadoLivreOAuthError("Seller mismatch.")
        existing = db.scalar(select(MercadoLivreConnectionModel).where(
            MercadoLivreConnectionModel.user_id == user_id,
            MercadoLivreConnectionModel.mercado_livre_user_id == seller_id))
        if existing is None:
            existing = MercadoLivreConnectionModel(
                user_id=user_id, mercado_livre_user_id=seller_id,
                access_token_encrypted="", refresh_token_encrypted="",
                expires_at=datetime.now(timezone.utc))
            db.add(existing)
        existing.nickname = str(profile.get("nickname") or "")
        existing.access_token_encrypted = encrypt_token(str(tokens["access_token"]))
        existing.refresh_token_encrypted = encrypt_token(str(tokens["refresh_token"]))
        existing.expires_at = token_expiration(tokens)
        key = f"ml:{seller_id}"
        source = db.scalar(select(CompanyDataSourceModel).where(
            CompanyDataSourceModel.company_id == user_id,
            CompanyDataSourceModel.provider == "mercado_livre",
            CompanyDataSourceModel.account_key == key))
        if source is None:
            source = CompanyDataSourceModel(
                company_id=user_id, provider="mercado_livre", account_key=key)
            db.add(source)
        source.display_name = existing.nickname or str(seller_id)
        source.enabled = True
        source.connected = True
        db.commit()
        return {"connected": True, "provider": "mercado_livre",
                "account_id": seller_id}
    except (httpx.HTTPError, MercadoLivreOAuthError, RuntimeError, ValueError, KeyError) as error:
        db.rollback()
        # Sem code/state/tokens: detalhes seguros apenas em logs privados do serviço.
        if isinstance(error, MercadoLivreOAuthError):
            logger.warning(
                "[mercado-livre-oauth] callback_failed phase=%s status=%s provider_error=%s",
                error.phase, error.http_status or "unknown", error.provider_error,
            )
        else:
            logger.warning(
                "[mercado-livre-oauth] callback_failed phase=storage_or_configuration error_type=%s",
                type(error).__name__,
            )
        raise HTTPException(502, "Falha ao concluir a autorização Mercado Livre.") from None


@router.get("")
def status(user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(MercadoLivreConnectionModel).where(
        MercadoLivreConnectionModel.user_id == user.id)).all()
    return [{"account_id": c.mercado_livre_user_id,
             "nickname": c.nickname, "expires_at": c.expires_at,
             "connected": True} for c in rows]


@router.get("/diagnostics")
def diagnostics(user: UserModel = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Diagnóstico por usuário: nunca responde com credenciais ou tokens."""
    accounts = db.scalars(select(MercadoLivreConnectionModel).where(
        MercadoLivreConnectionModel.user_id == user.id)).all()
    sources = db.scalars(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre")).all()
    sources_by_key = {s.account_key: s for s in sources}
    return {
        "app_credentials_configured": bool(
            settings.mercado_livre_client_id
            and settings.mercado_livre_client_id.get_secret_value()
            and settings.mercado_livre_client_secret
            and settings.mercado_livre_client_secret.get_secret_value()
        ),
        "redirect_uri": settings.mercado_livre_redirect_uri,
        "pkce_enabled": settings.mercado_livre_pkce_enabled,
        "connection_count": len(accounts),
        "accounts": [
            {
                "seller_id": a.mercado_livre_user_id,
                "nickname": a.nickname,
                "token_expires_at": a.expires_at,
                "source_exists": (source := sources_by_key.get(
                    f"ml:{a.mercado_livre_user_id}")) is not None,
                "source_connected": bool(source and source.connected),
                "source_enabled": bool(source and source.enabled),
                "last_synced_at": source.last_synced_at if source else None,
            }
            for a in accounts
        ],
    }


@router.get("/identity")
async def seller_identity(
    account_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the seller's public-facing display name and photo for their FluxHub tenant.

    OAuth tokens never leave the backend; image links are restricted to ML CDN.
    No schema change: this also works for sellers linked before the feature.
    """
    if account_id is None:
        account = db.scalar(
            select(MercadoLivreConnectionModel)
            .where(MercadoLivreConnectionModel.user_id == user.id)
            .order_by(MercadoLivreConnectionModel.id)
            .limit(1)
        )
        if account is None:
            raise HTTPException(404, "Nenhuma conta Mercado Livre vinculada.")
    else:
        account = get_account(db, user.id, account_id)
    profile_data = await seller_request(db, account, "/users/me")
    if not isinstance(profile_data, dict) or (
        int(profile_data.get("id") or 0) != account.mercado_livre_user_id
    ):
        raise HTTPException(502, "O perfil recebido não corresponde à conta vinculada.")
    identity = normalized_seller_identity(profile_data, account.mercado_livre_user_id)
    if identity["picture_url"] is None:
        # Public seller info can contain 'logo' even when /users/me omits it.
        # A photo is optional: unavailable logo must never break OAuth/login.
        try:
            public_data = await seller_request(
                db, account, f"/users/{account.mercado_livre_user_id}"
            )
            if isinstance(public_data, dict) and int(public_data.get("id") or 0) == account.mercado_livre_user_id:
                public_identity = normalized_seller_identity(
                    public_data, account.mercado_livre_user_id
                )
                identity["picture_url"] = public_identity["picture_url"]
        except HTTPException:
            pass
    return identity


@router.get("/profile")
async def profile(account_id: int | None = None,
                  user: UserModel = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    account = get_account(db, user.id, account_id)
    return await seller_request(db, account, "/users/me")


@router.get("/items")
async def items(account_id: int | None = None, limit: int = Query(default=50, ge=1, le=50),
                offset: int = Query(default=0, ge=0, le=1000),
                user: UserModel = Depends(get_current_user),
                db: Session = Depends(get_db)):
    account = get_account(db, user.id, account_id)
    return await seller_request(db, account,
        f"/users/{account.mercado_livre_user_id}/items/search",
        {"limit": limit, "offset": offset})


@router.get("/items/live")
async def live_items(
    account_id: int = Query(ge=1),
    scroll_id: str | None = Query(default=None, min_length=1, max_length=4096),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Page through the whole seller catalog without storing the returned items."""
    account = get_account(db, user.id, account_id)
    params = {"search_type": "scan", "limit": 50}
    if scroll_id:
        params["scroll_id"] = scroll_id
    page = await seller_request(
        db, account, f"/users/{account.mercado_livre_user_id}/items/search", params)
    if not isinstance(page, dict) or not isinstance(page.get("results"), list):
        raise HTTPException(502, "Lista de anúncios inválida.")
    ids = page["results"]
    if len(ids) > 50 or any(not isinstance(item_id, str) or
                            not item_id.isalnum() for item_id in ids):
        raise HTTPException(502, "IDs de anúncios inválidos.")
    cursor = page.get("scroll_id")
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096):
        raise HTTPException(502, "Cursor de anúncios inválido.")
    items = []
    for start in range(0, len(ids), 20):
        chunk = ids[start:start + 20]
        data = await seller_request(db, account, "/items/bulk", {"ids": ",".join(chunk)})
        if not isinstance(data, list):
            raise HTTPException(502, "Detalhes dos anúncios indisponíveis.")
        bodies = [row.get("body") for row in data if isinstance(row, dict)
                  and row.get("status_code", row.get("code")) == 200
                  and isinstance(row.get("body"), dict)]
        if len(bodies) != len(chunk) or {str(body.get("id")) for body in bodies} != set(chunk):
            raise HTTPException(502, "Lote incompleto; tente consultar novamente.")
        for body in bodies:
            if int(body.get("seller_id") or 0) != account.mercado_livre_user_id:
                raise HTTPException(502, "Anúncio de outro vendedor na resposta.")
            items.append({
                "id": str(body["id"]), "title": str(body.get("title") or "")[:255],
                "status": str(body.get("status") or "unknown")[:40],
                "price": body.get("price"), "stock": body.get("available_quantity"),
            })
    paging = page.get("paging")
    total = paging.get("total") if isinstance(paging, dict) else None
    return {
        "account_id": account.mercado_livre_user_id,
        "items": items,
        "next_scroll_id": cursor if ids else None,
        "total": total if type(total) is int and total >= 0 else None,
        "source": "mercado_livre_live",
    }


@router.get("/items/{item_id}")
async def item(item_id: str, account_id: int | None = None,
               user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    if not item_id.isalnum() or len(item_id) > 30:
        raise HTTPException(422, "Identificador inválido.")
    account = get_account(db, user.id, account_id)
    data = await seller_request(db, account, f"/items/{item_id}")
    if int(data.get("seller_id") or 0) != account.mercado_livre_user_id:
        raise HTTPException(404, "Anúncio não pertence à conta conectada.")
    return data


@router.get("/orders")
async def orders(account_id: int | None = None,
                 limit: int = Query(default=50, ge=1, le=50),
                 offset: int = Query(default=0, ge=0, le=1000),
                 user: UserModel = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    account = get_account(db, user.id, account_id)
    return await seller_request(db, account, "/orders/search",
        {"seller": account.mercado_livre_user_id, "limit": limit,
         "offset": offset, "sort": "date_desc"})


@router.get("/visits")
async def seller_visits(account_id: int | None = None,
                        days: int = Query(default=30, ge=1, le=150),
                        user: UserModel = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    account = get_account(db, user.id, account_id)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return await seller_request(db, account,
        f"/users/{account.mercado_livre_user_id}/items_visits",
        {"date_from": start.isoformat(), "date_to": end.isoformat()})


@router.get("/items/{item_id}/visits")
async def item_visits(item_id: str, account_id: int | None = None,
                      days: int = Query(default=30, ge=1, le=150),
                      user: UserModel = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    if not item_id.isalnum() or len(item_id) > 30:
        raise HTTPException(422, "Identificador inválido.")
    account = get_account(db, user.id, account_id)
    info = await seller_request(db, account, f"/items/{item_id}")
    if int(info.get("seller_id") or 0) != account.mercado_livre_user_id:
        raise HTTPException(404, "Anúncio não pertence à conta conectada.")
    return await seller_request(db, account,
        f"/items/{item_id}/visits/time_window", {"last": days, "unit": "day"})


@router.delete("")
def disconnect(account_id: int,
               user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    account = get_account(db, user.id, account_id)
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_livre",
        CompanyDataSourceModel.account_key == f"ml:{account_id}"))
    if source:
        source.enabled = False
        source.connected = False
    db.delete(account)
    db.commit()
    return {"disconnected": True, "account_id": account_id}
