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
