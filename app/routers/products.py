from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import CompanyDataSourceModel, UserModel, MercadoLivreListingPhotoModel
from app.database.repositories.products import ProductRepository
from app.database.session import get_db
from app.services.excel_import import (
    EXCEL_SOURCE_KEY,
    MAX_UPLOAD_BYTES,
    ExcelImportError,
    parse_product_workbook,
)
from app.services.products import ProductService


router = APIRouter(prefix="/products", tags=["Products"])


def _repository(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductRepository:
    return ProductRepository(db, company_id=current_user.id)


@router.post("/import/excel", status_code=status.HTTP_200_OK)
async def import_excel_products(
    file: UploadFile = File(...),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Envie uma planilha Excel .xlsx.",
        )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    try:
        products = parse_product_workbook(content)
    except ExcelImportError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    source = db.scalar(
        select(CompanyDataSourceModel).where(
            CompanyDataSourceModel.company_id == current_user.id,
            CompanyDataSourceModel.provider == "excel",
            CompanyDataSourceModel.account_key == EXCEL_SOURCE_KEY,
        )
    )
    if source is None:
        source = CompanyDataSourceModel(
            company_id=current_user.id,
            provider="excel",
            account_key=EXCEL_SOURCE_KEY,
            display_name="Planilha da empresa",
            enabled=True,
            connected=True,
        )
        db.add(source)
    source.display_name = file.filename
    source.enabled = True
    source.connected = True
    source.last_synced_at = datetime.now(timezone.utc)
    source.status_message = f"{len(products)} produtos importados."
    repository = ProductRepository(db, company_id=current_user.id)
    try:
        imported = repository.save_many(products, source_key=EXCEL_SOURCE_KEY)
    except Exception:
        db.rollback()
        raise
    return {
        "source": EXCEL_SOURCE_KEY,
        "imported": imported,
        "enabled": True,
        "message": f"Importação concluída: {imported} produtos da planilha.",
    }


@router.get("/photos")
def product_photos(
    ids: str = Query(min_length=3, max_length=12000),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item_ids = ids.split(",")
    if len(item_ids) > 100 or any(
        not item.isalnum() or len(item) > 100 for item in item_ids
    ):
        raise HTTPException(422, "Lista de produtos inválida.")
    records = db.scalars(select(MercadoLivreListingPhotoModel).where(
        MercadoLivreListingPhotoModel.user_id == user.id,
        MercadoLivreListingPhotoModel.external_id.in_(item_ids))).all()
    return [{"account_id": item.mercado_livre_user_id,
             "item_id": item.external_id,
             "thumbnail": item.photo_url} for item in records]


@router.get("/")
def get_products(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_products()


@router.get("/count")
def count_products(repository: ProductRepository = Depends(_repository)):
    return {"total_products": ProductService(repository).count_products()}


@router.get("/top-discount")
def get_top_discount(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_top_discount()


@router.get("/top-stock")
def get_top_stock(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_top_stock()


@router.get("/catalog-health")
def get_catalog_health(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_catalog_health()


@router.get("/categories")
def get_categories(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_categories()


@router.get("/brands")
def get_brands(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_brands()


@router.get("/out-of-stock")
def get_out_of_stock(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_out_of_stock()


@router.get("/low-stock")
def get_low_stock(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_low_stock()


@router.get("/low-rating")
def get_low_rating(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_low_rating()


@router.get("/top-rated")
def get_top_rated(repository: ProductRepository = Depends(_repository)):
    return ProductService(repository).get_top_rated()
