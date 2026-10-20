from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.database.models import UserModel


router = APIRouter(prefix="/stock", tags=["Stock"])


@router.get("/")
async def get_stock(_current_user: UserModel = Depends(get_current_user)):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="O estoque real ainda não foi sincronizado para esta empresa.",
    )