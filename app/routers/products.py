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
    

@router.get("/catalog-health")
def get_catalog_health():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_catalog_health()
    

@router.get("/categories")
def get_categories():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_categories()
    

@router.get("/brands")
def get_brands():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_brands()  
    

@router.get("/out-of-stock")
def get_out_of_stock():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_out_of_stock()

          
@router.get("/low-stock")
def get_low_stock():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_low_stock()

@router.get("/low-rating")
def get_low_rating():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_low_rating()

@router.get("/top-rated")
def get_top_rated():

    with Session(engine) as session:

        repository = ProductRepository(
            session
        )

        service = ProductService(
            repository
        )

        return service.get_top_rated()
    
    