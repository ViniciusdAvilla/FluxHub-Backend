"""FluxHub human sign-in; gated by server-to-server authentication."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.core.auth import get_current_user
from app.core.security import create_access_token, verify_password, hash_password
from app.database.models import UserModel
from app.database.session import get_db

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: LoginInput, db: Session = Depends(get_db)):
    user = db.scalar(select(UserModel).where(UserModel.email == body.email.strip().lower()))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    return {"access_token": create_access_token(user.id, user.token_version), "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60}


@router.get("/me")
def me(user: UserModel = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin,
            "is_active": user.is_active}


class ChangePassword(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/change-password")
def change_password(body: ChangePassword, user: UserModel = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Senha atual inválida.")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    db.commit()
    return {"ok": True, "message": "Faça login novamente."}
