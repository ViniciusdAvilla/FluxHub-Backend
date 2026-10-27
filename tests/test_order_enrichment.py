"""Regression guards for imported order items and privacy-safe buyer storage."""
from app.database.models import (
    MercadoLivreBuyerOrderModel,
    MercadoLivreOrderLineModel,
    MercadoLivreListingPhotoModel,
    MercadoLivreBuyerAccessAuditModel,
)
from app.routers.analytics import safe_public_thumbnail, thumbnail_from_item


def test_only_public_mercado_livre_images_are_accepted():
    assert safe_public_thumbnail("https://http2.mlstatic.com/D_12345.jpg")
    assert safe_public_thumbnail("http://http2.mlstatic.com/D_12345.jpg") is None
    assert safe_public_thumbnail("https://example.com/private") is None
    assert safe_public_thumbnail("javascript:alert(1)") is None
    assert thumbnail_from_item({
        "secure_thumbnail": "https://http2.mlstatic.com/D_12345.jpg",
    }) == "https://http2.mlstatic.com/D_12345.jpg"


def test_buyer_indexes_store_no_pii():
    names = {column.name for column in MercadoLivreBuyerOrderModel.__table__.columns}
    assert {"user_id", "mercado_livre_user_id", "order_external_id", "buyer_id"} <= names
    assert not names.intersection({
        "email", "phone", "cpf", "document_number", "address", "full_name",
    })


def test_audit_retains_no_private_fields():
    fields = {column.name for column in MercadoLivreBuyerAccessAuditModel.__table__.columns}
    assert fields == {"id", "user_id", "mercado_livre_user_id",
                      "order_external_id", "accessed_at"}


def test_order_line_and_photo_are_tenant_scoped():
    for model in (MercadoLivreOrderLineModel, MercadoLivreListingPhotoModel):
        names = {column.name for column in model.__table__.columns}
        assert {"user_id", "mercado_livre_user_id"} <= names
        assert any("user_id" in {column.name for column in constraint.columns}
                   for constraint in model.__table__.constraints
                   if hasattr(constraint, "columns"))


def test_enrichment_migration_is_additive_and_idempotent():
    """An upgrade must not delete original rows or reset seller credentials."""
    from sqlalchemy import create_engine, inspect, text
    from app.database.order_enrichment_schema import (
        ORDER_ENRICHMENT_TABLES, ensure_order_enrichment_schema,
    )

    db_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with db_engine.begin() as conn:
            conn.execute(text("CREATE TABLE fluxhub_users (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO fluxhub_users (id) VALUES (77)"))
        assert ensure_order_enrichment_schema(db_engine) == ORDER_ENRICHMENT_TABLES
        assert ensure_order_enrichment_schema(db_engine) == ORDER_ENRICHMENT_TABLES
        names = set(inspect(db_engine).get_table_names())
        assert set(ORDER_ENRICHMENT_TABLES) <= names
        with db_engine.connect() as conn:
            assert conn.scalar(text("SELECT id FROM fluxhub_users")) == 77
    finally:
        db_engine.dispose()


def test_pricing_endpoint_is_registered():
    """Prevent 404 when the frontend opens the product profitability calculator."""
    from app.main import app
    paths = set(app.openapi()["paths"])
    assert "/api/v1/pricing/{item_id}" in paths
    assert "/api/v1/pricing/{item_id}/cost" in paths


def test_paged_orders_with_enriched_line_and_buyer():
    """The 500-prone read path works after an additive schema bootstrap."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.database.models import (
        Base, MercadoLivreOrderSnapshotModel as Order,
        MercadoLivreOrderLineModel as OrderLine,
        MercadoLivreBuyerOrderModel as BuyerOrder,
        MercadoLivreListingPhotoModel as Photo,
    )
    from app.database.order_enrichment_schema import ensure_order_enrichment_schema
    from app.routers.orders import paged_orders

    db_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with db_engine.begin() as conn:
            conn.execute(text("CREATE TABLE fluxhub_users (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO fluxhub_users (id) VALUES (77)"))
        Base.metadata.tables["mercado_livre_order_snapshots"].create(bind=db_engine)
        Base.metadata.tables["mercado_livre_product_costs"].create(bind=db_engine)
        ensure_order_enrichment_schema(db_engine)
        with Session(db_engine) as db:
            db.add_all([
                Order(user_id=77, mercado_livre_user_id=436667093,
                      external_id="2000001", status="paid", total_amount=139.9,
                      created_at=datetime.now(timezone.utc), is_cancelled=False),
                OrderLine(user_id=77, mercado_livre_user_id=436667093,
                          order_external_id="2000001", line_number=0,
                          item_id="MLB999", title="Produto exemplo", sku="SKU-P",
                          quantity=1, unit_price=139.9, sale_fee=15.0),
                BuyerOrder(user_id=77, mercado_livre_user_id=436667093,
                           order_external_id="2000001", buyer_id=777000),
                Photo(user_id=77, mercado_livre_user_id=436667093,
                      external_id="MLB999",
                      photo_url="https://http2.mlstatic.com/D_12345.jpg"),
            ])
            db.commit()
            params = dict(
                account_id=436667093, limit=30, offset=0, days=None,
                status="paid", search="SKU-P", min_amount=100,
                max_amount=200, user=SimpleNamespace(id=77), db=db,
            )
            payload = paged_orders(**params)
            assert payload["total"] == 1
            assert payload["gross_paid_amount"] == 139.9
            assert payload["orders"][0]["items"][0]["sku"] == "SKU-P"
            assert payload["orders"][0]["buyer_id"] == 777000
            assert payload["orders"][0]["items"][0]["thumbnail"].startswith("https://")
            assert paged_orders(**{**params, "user": SimpleNamespace(id=99)})["total"] == 0
    finally:
        db_engine.dispose()


def test_sync_jobs_migration_adds_missing_columns_without_resetting_progress():
    """Existing sync checkpoints must survive a schema-only upgrade."""
    from sqlalchemy import create_engine, inspect, text
    from app.database.order_enrichment_schema import ensure_sync_job_schema

    db_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with db_engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE mercado_livre_sync_jobs "
                "(id INTEGER PRIMARY KEY, status VARCHAR(20), "
                "phase VARCHAR(20), listings_scanned INTEGER)"
            ))
            conn.execute(text(
                "INSERT INTO mercado_livre_sync_jobs "
                "(id, status, phase, listings_scanned) "
                "VALUES (1, 'running', 'listings', 6450)"
            ))
        assert ensure_sync_job_schema(db_engine) == (
            "pending_listing_ids", "pending_next_scroll_id",
        )
        ensure_sync_job_schema(db_engine)
        columns = {c["name"] for c in inspect(db_engine).get_columns(
            "mercado_livre_sync_jobs")}
        assert {"pending_listing_ids", "pending_next_scroll_id"} <= columns
        with db_engine.connect() as conn:
            assert conn.execute(text(
                "SELECT status, phase, listings_scanned "
                "FROM mercado_livre_sync_jobs WHERE id = 1"
            )).one() == ("running", "listings", 6450)
    finally:
        db_engine.dispose()
