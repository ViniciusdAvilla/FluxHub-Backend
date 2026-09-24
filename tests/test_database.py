from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.models import (
    Base,
    ProductModel,
    OrderModel,
    OrderItemModel,
)

from app.database.repositories.products import ProductRepository
from app.schemas.product import Product


def test_database_engine():
    assert engine is not None


def test_product_model():
    assert ProductModel.__tablename__ == "products"


def test_order_models():
    assert OrderModel.__tablename__ == "orders"
    assert OrderItemModel.__tablename__ == "order_items"

def test_product_repository():

    engine = create_engine(
        "sqlite:///:memory:"
    )

    Base.metadata.create_all(engine)

    with Session(engine) as session:

        repository = ProductRepository(session)

        product = Product(
            external_id="MLB999",
    title="Produto teste repository",
    category="Freios",
    brand="Bosch",
    original_price=170.00,
    promotional_price=150.00,
    discount_rate=0.12,
    stock_qty=50,
    sales_30d=20,
    rating=4.8,
    is_free_shipping=True,
    item_condition="Novo",
        )

        saved_product = repository.save(product)

        assert saved_product.id is not None
        assert saved_product.external_id == "MLB999"
        assert saved_product.title == "Produto teste repository"
        assert saved_product.promotional_price == 150.00    