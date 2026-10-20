from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.database.models import UserModel


router = APIRouter(prefix="/sales", tags=["Sales"])


@router.get("/")
async def get_sales(_current_user: UserModel = Depends(get_current_user)):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Série histórica de vendas ainda não está disponível.",
    )