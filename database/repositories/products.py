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