"""Mercado Pago seller OAuth; per-tenant read-only payments."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
from app.core.auth import get_current_user
from app.core.oauth_state import consume_state, issue_state
from app.core.security import encrypt_token
from app.database.session import get_db
from app.database.models import (
    UserModel, MercadoPagoConnectionModel, MercadoPagoOAuthStateModel,
    CompanyDataSourceModel,
)
from app.connectors.mercado_pago.auth import (
    API_URL, MercadoPagoError, active_token, authorization_url,
    expires_at, oauth_token,
)

router = APIRouter(prefix="/connectors/mercado-pago", tags=["Mercado Pago"])


def get_account(db: Session, user_id: int, account_id: str | None):
    query = select(MercadoPagoConnectionModel).where(
        MercadoPagoConnectionModel.user_id == user_id)
    if account_id is not None:
        query = query.where(MercadoPagoConnectionModel.mercado_pago_user_id == account_id)
    rows = db.scalars(query.order_by(MercadoPagoConnectionModel.id).limit(2)).all()
    if not rows:
        raise HTTPException(404, "Nenhuma conta Mercado Pago vinculada.")
    if len(rows) != 1:
        raise HTTPException(400, "Informe account_id para selecionar a conta.")
    return rows[0]


@router.get("/authorize")
def authorize(user: UserModel = Depends(get_current_user),
              db: Session = Depends(get_db)):
    try:
        state = issue_state(db, MercadoPagoOAuthStateModel, user.id)
        return {"authorization_url": authorization_url(state), "expires_in": 600}
    except RuntimeError:
        raise HTTPException(503, "Credenciais Mercado Pago não configuradas.")


@router.get("/callback")
async def callback(state: str = Query(min_length=16, max_length=200),
                   code: str | None = Query(default=None, max_length=2048),
                   error: str | None = Query(default=None, max_length=200),
                   db: Session = Depends(get_db)):
    user_id = consume_state(db, MercadoPagoOAuthStateModel, state)
    if error or not code:
        raise HTTPException(400, "Autorização Mercado Pago não concluída.")
    try:
        data = await oauth_token({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": __import__("app.config.settings", fromlist=["settings"]).settings.mercado_pago_redirect_uri})
        collector = str(data["user_id"])
        row = db.scalar(select(MercadoPagoConnectionModel).where(
            MercadoPagoConnectionModel.user_id == user_id,
            MercadoPagoConnectionModel.mercado_pago_user_id == collector))
        if row is None:
            row = MercadoPagoConnectionModel(
                user_id=user_id, mercado_pago_user_id=collector,
                access_token_encrypted="", refresh_token_encrypted="",
                expires_at=expires_at(data))
            db.add(row)
        row.access_token_encrypted = encrypt_token(str(data["access_token"]))
        row.refresh_token_encrypted = encrypt_token(str(data["refresh_token"]))
        row.expires_at = expires_at(data)
        source = db.scalar(select(CompanyDataSourceModel).where(
            CompanyDataSourceModel.company_id == user_id,
            CompanyDataSourceModel.provider == "mercado_pago",
            CompanyDataSourceModel.account_key == f"mp:{collector}"))
        if source is None:
            source = CompanyDataSourceModel(
                company_id=user_id, provider="mercado_pago",
                account_key=f"mp:{collector}")
            db.add(source)
        source.display_name = f"Mercado Pago {collector}"
        source.connected = True
        source.enabled = True
        db.commit()
        return {"connected": True, "provider": "mercado_pago", "account_id": collector}
    except (MercadoPagoError, RuntimeError, ValueError, KeyError):
        db.rollback()
        raise HTTPException(502, "Falha ao concluir a autorização Mercado Pago.")


@router.get("")
def status(user: UserModel = Depends(get_current_user),
           db: Session = Depends(get_db)):
    rows = db.scalars(select(MercadoPagoConnectionModel).where(
        MercadoPagoConnectionModel.user_id == user.id)).all()
    return [{"account_id": row.mercado_pago_user_id, "connected": True,
             "expires_at": row.expires_at} for row in rows]


@router.get("/payments")
async def payments(account_id: str | None = None,
                   limit: int = Query(default=20, ge=1, le=50),
                   offset: int = Query(default=0, ge=0, le=1000),
                   user: UserModel = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = get_account(db, user.id, account_id)
    try:
        token = await active_token(row, db)
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                f"{API_URL}/v1/payments/search",
                params={"limit": limit, "offset": offset},
                headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            return response.json()
    except (MercadoPagoError, httpx.HTTPError, RuntimeError, ValueError):
        raise HTTPException(502, "Falha ao consultar pagamentos.")


@router.delete("")
def disconnect(account_id: str, user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    row = get_account(db, user.id, account_id)
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == "mercado_pago",
        CompanyDataSourceModel.account_key == f"mp:{account_id}"))
    if source:
        source.enabled = False
        source.connected = False
    db.delete(row)
    db.commit()
    return {"disconnected": True, "account_id": account_id}
