from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import CompanyDataSourceModel, ProductModel
from app.schemas.product import Product


class ProductRepository:

    def __init__(self, session: Session, company_id: int | None = None):
        self.session = session
        self.company_id = company_id

    def save(self, product: Product) -> ProductModel:

        product_model = ProductModel(
            external_id=product.external_id,
            title=product.title,
            original_price=product.original_price,
            promotional_price=product.promotional_price,
            discount_rate=product.discount_rate,
            category=product.category,
            brand=product.brand,
            stock_qty=product.stock_qty,
            sales_30d=product.sales_30d,
            rating=product.rating,
            is_free_shipping=product.is_free_shipping,
            item_condition=product.item_condition,
)

        self.session.add(product_model)
        self.session.commit()
        self.session.refresh(product_model)

        return product_model

<<<<<<< HEAD
    def save_many(
        self,
        products: list[Product],
        source_key: str = "legacy",
    ) -> int:
            try:
                existing = self.session.query(ProductModel)
                if self.company_id is None:
                    existing = existing.filter(ProductModel.company_id.is_(None))
                else:
                    existing = existing.filter(
                        ProductModel.company_id == self.company_id
                    )
                existing.filter(ProductModel.source_key == source_key).delete(
                    synchronize_session=False
                )
=======
    def save_many(self, products: list[Product]) -> int:
            try:
                self.session.query(ProductModel).delete(
                    synchronize_session=False
                )

                product_models = []

                for product in products:
                    product_model = ProductModel(
                        external_id=product.external_id,
                        title=product.title,
                        category=product.category,
                        brand=product.brand,
                        original_price=product.original_price,
                        promotional_price=product.promotional_price,
                        discount_rate=product.discount_rate,
                        stock_qty=product.stock_qty,
                        sales_30d=product.sales_30d,
                        rating=product.rating,
                        is_free_shipping=product.is_free_shipping,
                        item_condition=product.item_condition,
                    )

                    product_models.append(product_model)

                self.session.add_all(product_models)
                self.session.commit()

                return len(product_models)

            except Exception:
                self.session.rollback()
                raise
        
    def get_all(self):
>>>>>>> 0f0be375cc57f2cc8768c8753476903afcddb5c7

                product_models = []

                for product in products:
                    product_model = ProductModel(
                        external_id=product.external_id,
                        company_id=self.company_id,
                        source_key=source_key,
                        title=product.title,
                        category=product.category,
                        brand=product.brand,
                        original_price=product.original_price,
                        promotional_price=product.promotional_price,
                        discount_rate=product.discount_rate,
                        stock_qty=product.stock_qty,
                        sales_30d=product.sales_30d,
                        rating=product.rating,
                        is_free_shipping=product.is_free_shipping,
                        item_condition=product.item_condition,
                    )

                    product_models.append(product_model)

                self.session.add_all(product_models)
                self.session.commit()

                return len(product_models)

            except Exception:
                self.session.rollback()
                raise

    def get_all(self) -> list[ProductModel]:
        query = self.session.query(ProductModel)
        if self.company_id is None:
            return query.all()

        enabled_sources = select(CompanyDataSourceModel.account_key).where(
            CompanyDataSourceModel.company_id == self.company_id,
            CompanyDataSourceModel.enabled.is_(True),
        )
        products = query.filter(
            ProductModel.company_id == self.company_id,
            ProductModel.source_key.in_(enabled_sources),
        ).order_by(ProductModel.id).all()
        unique_products: dict[str, ProductModel] = {}
        for product in products:
            unique_products.setdefault(product.external_id, product)
        return list(unique_products.values())

    def get_indicators(self):

        products = self.get_all()

        if not products:
            return {
                "total_products": 0,
                "avg_original_price": None,
                "avg_promotional_price": None,
                "avg_discount": None,
                "total_stock": 0,
                "avg_rating": None,
            }
        def average(values: list[float | int | None]) -> float | None:
            known = [value for value in values if value is not None]
            return sum(known) / len(known) if known else None

        return {
            "total_products": len(products),
            "avg_original_price": average(
                [product.original_price for product in products]
            ),
            "avg_promotional_price": average(
                [product.promotional_price for product in products]
            ),
            "avg_discount": average(
                [product.discount_rate for product in products]
            ),
            "total_stock": (
                sum(product.stock_qty for product in products)
                if all(product.stock_qty is not None for product in products)
                else None
            ),
            "avg_rating": average([product.rating for product in products]),
        }

    def get_top_discount(self, limit: int = 10):
        return sorted(
            self.get_all(),
            key=lambda product: (
                product.discount_rate
                if product.discount_rate is not None
                else float("-inf")
            ),
            reverse=True,
        )[:limit]

    def count_products(self) -> int:

        return len(self.get_all())



    def get_top_stock(
        self, limit: int = 10):
        return sorted(
            self.get_all(),
            key=lambda product: (
                product.stock_qty
                if product.stock_qty is not None
                else float("-inf")
            ),
            reverse=True,
        )[:limit]

    def get_catalog_health(self):

        products = self.get_all()

        return {
        "out_of_stock": len([
            p for p in products
            if p.stock_qty is not None and p.stock_qty == 0
        ]),

        "low_stock": len([
            p for p in products
            if p.stock_qty is not None and p.stock_qty <= 10
        ]),

        "low_rating": len([
            p for p in products
            if p.rating is not None and p.rating < 4.0
        ]),

        "without_discount": len([
            p for p in products
            if p.discount_rate is not None and p.discount_rate == 0
        ]),

        "without_free_shipping": len([
            p for p in products
            if p.is_free_shipping is False
        ]),

        "without_category": len([
            p for p in products
            if not p.category
        ]),

        "without_brand": len([
            p for p in products
            if not p.brand
        ]),

        "used_products": len([
            p for p in products
            if p.item_condition and p.item_condition != "new"
        ])
    }

    def get_categories(self):

        products = self.get_all()

        categories = {}

        for product in products:

            category = product.category

            if not category:
                category = "Sem Categoria"

            categories[category] = (
                categories.get(category, 0) + 1
            )

        result = []

        for category, count in categories.items():

            result.append(
                {
                    "category": category,
                    "count": count
                }
            )

        result.sort(
            key=lambda x: x["count"],
            reverse=True
        )

        return result   
    
    
    def get_brands(self):

        products = self.get_all()

        brands = {}

        for product in products:

            brand = product.brand

            if not brand:
                brand = "Sem Marca"

            brands[brand] = (
                brands.get(brand, 0) + 1
            )

        result = []

        for brand, count in brands.items():

            result.append(
                {
                    "brand": brand,
                    "count": count
                }
            )

        result.sort(
            key=lambda x: x["count"],
            reverse=True
        )

        return result
    

    def get_out_of_stock(self):

         return [
        {
            "external_id": product.external_id,
            "title": product.title,
            "brand": product.brand,
            "category": product.category,
            "stock_qty": product.stock_qty
        }
        for product in self.get_all()
        if product.stock_qty == 0
    ]

    def get_low_stock(
        self,
        threshold: int = 10
    ):

        return [
            {
                "external_id": product.external_id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category,
                "stock_qty": product.stock_qty
            }
            for product in self.get_all()
            if product.stock_qty is not None and product.stock_qty <= threshold
        ]

    def get_low_rating(
        self,
        threshold: float = 4.5
    ):

        return [
            {
                "external_id": product.external_id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category,
                "rating": product.rating
            }
            for product in self.get_all()
            if product.rating is not None and product.rating < threshold
        ]           

    def get_top_rated(
        self,
        limit: int = 10
    ):

        products = sorted(
            (product for product in self.get_all() if product.rating is not None),
            key=lambda p: p.rating if p.rating is not None else float("-inf"),
            reverse=True
        )

        return [
            {
                "external_id": product.external_id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category,
                "rating": product.rating
            }
            for product in products[:limit]
        ]    