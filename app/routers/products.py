from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.repositories.products import ProductRepository
from app.services.products import ProductService


router = APIRouter(
    prefix="/products",
    tags=["Products"],
)


@router.get("/")
def get_products():

    with Session(engine) as session:

        repository = ProductRepository(session)

        service = ProductService(
            repository
        )

        return service.get_products()


@router.get("/count")
def count_products():

    with Session(engine) as session:

        repository = ProductRepository(session)

        service = ProductService(
            repository
        )

        return {
            "total_products": (
                service.count_products()
            )
        }


@router.get("/top-discount")
def get_top_discount():

    with Session(engine) as session:

        repository = ProductRepository(session)

        service = ProductService(
            repository
        )

        return service.get_top_discount()


@router.get("/top-stock")
def get_top_stock():

    with Session(engine) as session:

        repository = ProductRepository(session)

        service = ProductService(
            repository
        )

        return service.get_top_stock()