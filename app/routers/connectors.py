"""User-scoped integration controls for future frontends."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import UserModel, CompanyDataSourceModel
from app.database.session import get_db

router = APIRouter(prefix="/connectors", tags=["Connectors"])


class SourceUpdate(BaseModel):
    enabled: bool


@router.get("")
def list_sources(user: UserModel = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    rows = db.scalars(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id)).all()
    return [{"provider": r.provider, "account_key": r.account_key,
             "enabled": r.enabled, "connected": r.connected,
             "display_name": r.display_name, "last_synced_at": r.last_synced_at}
            for r in rows]


@router.patch("/{provider}/{account_key}")
def toggle_source(provider: str, account_key: str, body: SourceUpdate,
                  user: UserModel = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    source = db.scalar(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user.id,
        CompanyDataSourceModel.provider == provider,
        CompanyDataSourceModel.account_key == account_key))
    if source is None:
        raise HTTPException(404, "Fonte não encontrada.")
    if body.enabled and not source.connected:
        raise HTTPException(409, "Conecte a fonte antes de ativá-la.")
    source.enabled = body.enabled
    db.commit()
    return {"provider": provider, "account_key": account_key, "enabled": source.enabled}
