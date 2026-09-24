from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.repositories.products import ProductRepository
from app.services.indicators import IndicatorService


router = APIRouter(
    prefix="/indicators",
    tags=["Indicators"]
)

@router.get("/")
async def get_indicators():

    return {
        "total_orders": 125,
        "total_revenue": 32549.90,
        "average_ticket": 260.40,
        "products_sold": 412
    }