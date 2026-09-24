from app.connectors.excel.products import ExcelProducts
from app.database.repositories.products import ProductRepository


class ProductImportService:

    def __init__(
        self,
        products_connector: ExcelProducts,
        repository: ProductRepository,
    ):
        self.products_connector = products_connector
        self.repository = repository

    def import_products(self) -> int:

        products = self.products_connector.get_products()

        total = self.repository.save_many(products)

        return total