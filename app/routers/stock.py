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
    products = ProductRepository(db, company_id=user.id).get_all()
    return {
        "total_products": len(products),
        "total_stock": sum(p.stock_qty for p in products
                           if p.stock_qty is not None),
        "unknown_stock_items": sum(p.stock_qty is None for p in products),
        "out_of_stock": sum(p.stock_qty == 0 for p in products),
        "low_stock": sum(p.stock_qty is not None and 0 < p.stock_qty <= 10 for p in products),
        "source": "stored_catalog",
    }
