from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.repositories.products import ProductRepository
from app.services.dashboard import DashboardService


router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get("")
def get_dashboard():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = DashboardService(
            repository
        )

        return service.get_dashboard()