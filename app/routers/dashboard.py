from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import UserModel
from app.database.repositories.products import ProductRepository
from app.database.session import get_db
from app.services.dashboard import DashboardService


router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get("")
def get_dashboard(
    current_user: UserModel = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repository = ProductRepository(session, company_id=current_user.id)
    return DashboardService(repository).get_dashboard()