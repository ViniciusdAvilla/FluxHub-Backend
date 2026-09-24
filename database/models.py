from sqlalchemy import String, Float, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass

class ProductModel(Base):

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)

    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True
    )

    title: Mapped[str] = mapped_column(String(255))

    category: Mapped[str] = mapped_column(
        String(100)
    )

    brand: Mapped[str] = mapped_column(
        String(100)
    )

    original_price: Mapped[float] = mapped_column(Float)

    promotional_price: Mapped[float] = mapped_column(Float)

    discount_rate: Mapped[float] = mapped_column(Float)

    stock_qty: Mapped[int] = mapped_column()

    sales_30d: Mapped[int] = mapped_column()

    rating: Mapped[float] = mapped_column(Float)

    is_free_shipping: Mapped[bool] = mapped_column()

    item_condition: Mapped[str] = mapped_column(
        String(50)
    )