"""Order snapshots; no assertion of settled payment revenue."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.database.models import UserModel, MercadoLivreOrderSnapshotModel
from app.database.session import get_db

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.get("/")
def sales(days: int = Query(default=30, ge=1, le=365),
          user: UserModel = Depends(get_current_user),
          db: Session = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(select(MercadoLivreOrderSnapshotModel).where(
        MercadoLivreOrderSnapshotModel.user_id == user.id,
        MercadoLivreOrderSnapshotModel.created_at >= cutoff)).all()
    non_cancelled = [r for r in rows if not r.is_cancelled]
    return {"days": days, "total_orders": len(rows),
            "non_cancelled_orders": len(non_cancelled),
            "gross_non_cancelled_order_amount": round(sum(
                r.total_amount for r in non_cancelled), 2),
            "note": "Valor bruto de pedidos registrados, não receita financeira liquidada."}
