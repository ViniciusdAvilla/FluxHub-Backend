"""Administrator-only JSON API for a future admin dashboard."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.security import hash_password
from app.database.models import UserModel, CompanyDataSourceModel
from app.database.session import get_db

router = APIRouter(prefix="/admin", tags=["Admin"])


def require_admin(user: UserModel = Depends(get_current_user)) -> UserModel:
    if not user.is_active or not user.is_admin:
        raise HTTPException(403, "Somente administradores.")
    return user


class CreateClient(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)


class UserStatus(BaseModel):
    is_active: bool


class ResetPassword(BaseModel):
    password: str = Field(min_length=12, max_length=256)


def show(user: UserModel) -> dict:
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin,
            "is_active": user.is_active, "created_at": user.created_at}


@router.post("/users", status_code=201)
def create_user(body: CreateClient, _admin: UserModel = Depends(require_admin),
                db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@"):
        raise HTTPException(422, "E-mail inválido.")
    user = UserModel(email=email, password_hash=hash_password(body.password),
                     is_admin=False, is_active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "E-mail já cadastrado.")
    db.refresh(user)
    return show(user)


@router.get("/users")
def list_users(_admin: UserModel = Depends(require_admin),
               db: Session = Depends(get_db), limit: int = 100, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "Paginação inválida.")
    users = db.scalars(select(UserModel).order_by(UserModel.id)
                       .offset(offset).limit(limit)).all()
    return [show(user) for user in users]


@router.patch("/users/{user_id}/status")
def set_user_status(user_id: int, body: UserStatus,
                    admin: UserModel = Depends(require_admin),
                    db: Session = Depends(get_db)):
    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(404, "Usuário não encontrado.")
    if user.id == admin.id and not body.is_active:
        raise HTTPException(400, "Não pode desativar sua própria conta.")
    user.is_active = body.is_active
    db.commit()
    return show(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPassword,
                   _admin: UserModel = Depends(require_admin),
                   db: Session = Depends(get_db)):
    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(404, "Usuário não encontrado.")
    user.password_hash = hash_password(body.password)
    user.token_version += 1
    db.commit()
    return {"ok": True}


@router.get("/users/{user_id}/sources")
def user_sources(user_id: int, _admin: UserModel = Depends(require_admin),
                 db: Session = Depends(get_db)):
    if db.get(UserModel, user_id) is None:
        raise HTTPException(404, "Usuário não encontrado.")
    sources = db.scalars(select(CompanyDataSourceModel).where(
        CompanyDataSourceModel.company_id == user_id)).all()
    return [{"provider": s.provider, "account_key": s.account_key,
             "connected": s.connected, "enabled": s.enabled,
             "display_name": s.display_name, "last_synced_at": s.last_synced_at}
            for s in sources]
