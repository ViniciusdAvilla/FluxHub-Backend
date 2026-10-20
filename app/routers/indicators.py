"""Catalog indicators, derived from connected and enabled tenant sources."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import UserModel
from app.database.repositories.products import ProductRepository
from app.database.session import get_db

router = APIRouter(prefix="/indicators", tags=["Indicators"])


@router.get("/")
def indicators(user: UserModel = Depends(get_current_user),
               db: Session = Depends(get_db)):
    return ProductRepository(db, company_id=user.id).get_indicators()
