from sqlalchemy.orm import Session

from app.database.models import ProductModel
from app.schemas.product import Product


class ProductRepository:

    def __init__(self, session: Session):
        self.session = session

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

        return self.session.query(
            ProductModel
        ).all()

    def count_products(self) -> int:

        total = self.session.query(
            ProductModel
        ).count()

        return total


    def get_indicators(self):

        products = self.get_all()

        return {
        "total_products": len(products),
        "avg_original_price": (
            sum(p.original_price for p in products)
            / len(products)
        ),
        "avg_promotional_price": (
            sum(p.promotional_price for p in products)
            / len(products)
        ),
        "avg_discount": (
            sum(p.discount_rate for p in products)
            / len(products)
        ),
        "total_stock": (
            sum(p.stock_qty for p in products)
        ),
        "avg_rating": (
            sum(p.rating for p in products)
            / len(products)
        ),
    }

    def get_top_discount(self, limit: int = 10):

        return (
        self.session.query(ProductModel)
        .order_by(
            ProductModel.discount_rate.desc()
        )
        .limit(limit)
        .all()
    )

    def count_products(self) -> int:

        return self.session.query(
        ProductModel
        ).count()



    def get_top_stock(
        self, limit: int = 10):

        return (self.session.query(
                ProductModel
            )
            .order_by(
                ProductModel.stock_qty.desc()
            )
            .limit(limit)
            .all()
        )

    def get_top_discount(self, limit: int = 10):

        return (self.session.query(ProductModel)
            .order_by(
                ProductModel.discount_rate.desc()
            )
            .limit(limit)
            .all()
        )
    def get_catalog_health(self):

        products = self.get_all()

        return {
        "out_of_stock": len([
            p for p in products
            if p.stock_qty == 0
        ]),

        "low_stock": len([
            p for p in products
            if p.stock_qty <= 10
        ]),

        "low_rating": len([
            p for p in products
            if p.rating < 4.0
        ]),

        "without_discount": len([
            p for p in products
            if p.discount_rate == 0
        ]),

        "without_free_shipping": len([
            p for p in products
            if not p.is_free_shipping
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
            if p.item_condition != "new"
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
            if product.stock_qty <= threshold
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
            if product.rating < threshold
        ]           

    def get_top_rated(
        self,
        limit: int = 10
    ):

        products = sorted(
            self.get_all(),
            key=lambda p: p.rating,
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