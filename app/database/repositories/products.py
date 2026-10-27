from sqlalchemy import case, func, select
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

    def get_catalog_summary(self) -> dict:
        """Aggregate visible products in SQL; preserve first-ID deduplication.

        ProductRepository.get_all() intentionally keeps one row per external ID
        across enabled company sources, ordered by the oldest database row.
        A window subquery applies that same rule without materializing 10k+
        ORM instances just to calculate dashboard totals.
        """
        if self.company_id is None:
            visible_ids = select(ProductModel.id)
        else:
            enabled_sources = select(CompanyDataSourceModel.account_key).where(
                CompanyDataSourceModel.company_id == self.company_id,
                CompanyDataSourceModel.enabled.is_(True),
            )
            ranked = select(
                ProductModel.id.label("product_id"),
                func.row_number().over(
                    partition_by=ProductModel.external_id,
                    order_by=ProductModel.id,
                ).label("position"),
            ).where(
                ProductModel.company_id == self.company_id,
                ProductModel.source_key.in_(enabled_sources),
            ).subquery()
            visible_ids = select(ranked.c.product_id).where(ranked.c.position == 1)

        total, stock_sum, known_stock, original, promotion, discount, rating, out_stock, low_stock = (
            self.session.execute(
                select(
                    func.count(ProductModel.id),
                    func.sum(ProductModel.stock_qty),
                    func.count(ProductModel.stock_qty),
                    func.avg(ProductModel.original_price),
                    func.avg(ProductModel.promotional_price),
                    func.avg(ProductModel.discount_rate),
                    func.avg(ProductModel.rating),
                    func.sum(case((ProductModel.stock_qty == 0, 1), else_=0)),
                    func.sum(case((
                        (ProductModel.stock_qty > 0) & (ProductModel.stock_qty <= 10),
                        1,
                    ), else_=0)),
                ).where(ProductModel.id.in_(visible_ids))
            ).one()
        )
        return {
            "total_products": int(total),
            "total_stock_known": int(stock_sum or 0),
            "unknown_stock_items": int(total - known_stock),
            "out_of_stock": int(out_stock or 0),
            "low_stock": int(low_stock or 0),
            "avg_original_price": float(original) if original is not None else None,
            "avg_promotional_price": float(promotion) if promotion is not None else None,
            "avg_discount": float(discount) if discount is not None else None,
            "avg_rating": float(rating) if rating is not None else None,
        }

    def get_indicators(self):
        summary = self.get_catalog_summary()
        return {
            "total_products": summary["total_products"],
            "avg_original_price": summary["avg_original_price"],
            "avg_promotional_price": summary["avg_promotional_price"],
            "avg_discount": summary["avg_discount"],
            "total_stock": (
                summary["total_stock_known"]
                if summary["unknown_stock_items"] == 0 else None
            ),
            "avg_rating": summary["avg_rating"],
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

        return self.get_catalog_summary()["total_products"]



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
