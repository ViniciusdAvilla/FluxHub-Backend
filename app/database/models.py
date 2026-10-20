from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass

class ProductModel(Base):

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "source_key", "external_id", name="uq_product_company_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    external_id: Mapped[str] = mapped_column(
        String(100),
        index=True,
    )

    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_key: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))

    category: Mapped[str] = mapped_column(
        String(100)
    )

    brand: Mapped[str] = mapped_column(
        String(100)
    )

    original_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    promotional_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    discount_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    stock_qty: Mapped[int | None] = mapped_column(nullable=True)

    sales_30d: Mapped[int | None] = mapped_column(nullable=True)

    rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_free_shipping: Mapped[bool | None] = mapped_column(nullable=True)

    item_condition: Mapped[str] = mapped_column(
        String(50)
    )


class UserModel(Base):
    __tablename__ = "fluxhub_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, server_default="true")
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CompanyDataSourceModel(Base):
    __tablename__ = "company_data_sources"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "provider", "account_key", name="uq_company_data_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40), index=True)
    account_key: Mapped[str] = mapped_column(String(150))
    display_name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=False)
    connected: Mapped[bool] = mapped_column(default=False)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MercadoLivreConnectionModel(Base):
    __tablename__ = "mercado_livre_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "mercado_livre_user_id", name="uq_ml_connection_company_account"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"),
        index=True,
    )
    mercado_livre_user_id: Mapped[int] = mapped_column(index=True)
    nickname: Mapped[str | None] = mapped_column(String(150), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MercadoLivreOAuthStateModel(Base):
    __tablename__ = "mercado_livre_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MercadoLivreListingSnapshotModel(Base):
    __tablename__ = "mercado_livre_listing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "mercado_livre_user_id",
            "external_id",
            name="uq_ml_listing_user_account_item",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True
    )
    mercado_livre_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    listing_type_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price: Mapped[float] = mapped_column(Float)
    currency_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    visits_30d: Mapped[int] = mapped_column(Integer, default=0)
    units_sold_30d: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MercadoLivreProductCostModel(Base):
    __tablename__ = "mercado_livre_product_costs"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="uq_ml_cost_user_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    unit_cost: Mapped[float] = mapped_column(Float)
    marketplace_fee_percent: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    manufacturer: Mapped[str] = mapped_column(String(120), default="")
    tax_percent: Mapped[float] = mapped_column(Float, default=0)
    shipping_cost: Mapped[float] = mapped_column(Float, default=0)
    other_cost: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MercadoLivreOrderSnapshotModel(Base):
    __tablename__ = "mercado_livre_order_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "mercado_livre_user_id",
            "external_id",
            name="uq_ml_order_user_account_order",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True
    )
    mercado_livre_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), index=True)
    total_amount: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_cancelled: Mapped[bool] = mapped_column(default=False, index=True)


class MercadoLivreAdsDailySnapshotModel(Base):
    __tablename__ = "mercado_livre_ads_daily_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "mercado_livre_user_id",
            "external_id",
            "report_date",
            name="uq_ml_ads_user_account_item_day",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True
    )
    mercado_livre_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    report_date: Mapped[date] = mapped_column(Date, index=True)
    attributed_sales: Mapped[int] = mapped_column(Integer, default=0)
    ad_spend: Mapped[float] = mapped_column(Float, default=0)
    attributed_revenue: Mapped[float] = mapped_column(Float, default=0)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MercadoLivreListingCancellationSnapshotModel(Base):
    __tablename__ = "mercado_livre_listing_cancellation_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "mercado_livre_user_id",
            "external_id",
            name="uq_ml_listing_cancel_user_account_item",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True
    )
    mercado_livre_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(100))
    canceled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

class MercadoPagoConnectionModel(Base):
    __tablename__ = "mercado_pago_connections"
    __table_args__ = (UniqueConstraint("user_id", "mercado_pago_user_id",
                                       name="uq_mp_connection_user_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True)
    mercado_pago_user_id: Mapped[str] = mapped_column(String(100), index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MercadoPagoOAuthStateModel(Base):
    __tablename__ = "mercado_pago_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("fluxhub_users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
