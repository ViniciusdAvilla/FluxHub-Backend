"""User stock from locally synchronized/Excel catalog; not live platform inventory."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import UserModel
from app.database.repositories.products import ProductRepository
from app.database.session import get_db

router = APIRouter(prefix="/stock", tags=["Stock"])


@router.get("/")
def stock(user: UserModel = Depends(get_current_user),
          db: Session = Depends(get_db)):
    summary = ProductRepository(db, company_id=user.id).get_catalog_summary()
    return {
        "total_products": summary["total_products"],
        "total_stock": summary["total_stock_known"],
        "unknown_stock_items": summary["unknown_stock_items"],
        "out_of_stock": summary["out_of_stock"],
        "low_stock": summary["low_stock"],
        "source": "stored_catalog",
    }
