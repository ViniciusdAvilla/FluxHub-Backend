from sqlalchemy.orm import Session

from app.database.connection import engine

from app.database.repositories.products import ProductRepository
from app.connectors.excel.products import ExcelProducts
from app.services.product_import import ProductImportService


with Session(engine) as session:

    connector = ExcelProducts(
        "app/connectors/excel/data/shopping_dos_utilitarios_1000_produtos.xlsx"
    )

    repository = ProductRepository(session)

    service = ProductImportService(
        connector,
        repository,
    )

    total = service.import_products()

    print(f"{total} produtos importados.")
    