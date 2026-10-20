from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.database.models import UserModel


router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("/{order_id}")
async def get_order(
    order_id: str, _current_user: UserModel = Depends(get_current_user)
):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Dados de pedidos reais ainda não estão disponíveis nesta integração.",
    )


@router.get("/")
async def list_orders(_current_user: UserModel = Depends(get_current_user)):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Dados de pedidos reais ainda não estão disponíveis nesta integração.",
    )