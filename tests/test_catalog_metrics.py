"""Real catalog metrics must remain tenant-scoped and must not invent Ads costs."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.models import (
    MercadoLivreAdsDailySnapshotModel as Ads,
    MercadoLivreConnectionModel as Connection,
    MercadoLivreOrderLineModel as Line,
    MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreProductCostModel as Cost,
    MercadoLivreSyncJobModel as Job,
)
from app.routers.analytics import catalog_metrics


def test_catalog_metrics_real_paid_units_and_tenant_isolation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE fluxhub_users (id INTEGER PRIMARY KEY)"))
        db.execute(text("INSERT INTO fluxhub_users (id) VALUES (7), (8)"))
    for model in (Connection, Order, Line, Cost, Ads, Job):
        model.__table__.create(bind=engine)
    now = datetime.now(timezone.utc)

    def order(tenant, seller, oid, status="paid", cancelled=False, days_old=1):
        return Order(user_id=tenant, mercado_livre_user_id=seller,
                     external_id=oid, status=status, total_amount=100,
                     created_at=now - timedelta(days=days_old),
                     is_cancelled=cancelled)

    def line(tenant, seller, oid, item, quantity):
        return Line(user_id=tenant, mercado_livre_user_id=seller,
                    order_external_id=oid, line_number=0, item_id=item,
                    title="Produto", quantity=quantity, unit_price=50)

    try:
        with Session(engine) as db:
            db.add_all([
                Connection(user_id=7, mercado_livre_user_id=101,
                           access_token_encrypted="a", refresh_token_encrypted="b",
                           expires_at=now),
                Connection(user_id=7, mercado_livre_user_id=102,
                           access_token_encrypted="a", refresh_token_encrypted="b",
                           expires_at=now),
                Connection(user_id=8, mercado_livre_user_id=201,
                           access_token_encrypted="a", refresh_token_encrypted="b",
                           expires_at=now),
                order(7, 101, "A"), line(7, 101, "A", "MLB1", 3),
                order(7, 101, "B", status="cancelled", cancelled=True),
                line(7, 101, "B", "MLB1", 50),
                order(7, 101, "C", days_old=40),
                line(7, 101, "C", "MLB1", 70),
                order(7, 101, "D"),  # Legacy order missing lines.
                order(7, 102, "E"), line(7, 102, "E", "MLB2", 2),
                order(8, 201, "F"), line(8, 201, "F", "MLB1", 90),
                Cost(user_id=7, external_id="MLB1", unit_cost=18, manufacturer="Fábrica"),
                Cost(user_id=8, external_id="MLB1", unit_cost=999, manufacturer="Outro"),
                Ads(user_id=7, mercado_livre_user_id=101, external_id="MLB1",
                    report_date=date.today(), attributed_sales=1,
                    ad_spend=10, attributed_revenue=90),
                Job(user_id=7, mercado_livre_user_id=101, status="completed",
                    phase="done"),
                Job(user_id=7, mercado_livre_user_id=102, status="completed",
                    phase="done"),
            ])
            db.commit()
            result = catalog_metrics(days=30, account_id=101,
                                     user=SimpleNamespace(id=7), db=db)
            assert result["sales_by_item"] == {"MLB1": 3}
            assert result["paid_orders"] == 2
            assert result["enriched_paid_orders"] == 1
            assert result["orders_complete"] is False
            assert result["costs"] == [
                {"external_id": "MLB1", "unit_cost": 18, "manufacturer": "Fábrica"}
            ]
            assert result["ads_by_item"]["MLB1"] == {
                "ad_spend": 10.0, "attributed_revenue": 90.0
            }
            assert result["ads_source"] == "imported_reports_only"

            with pytest.raises(HTTPException) as ex:
                catalog_metrics(days=30, account_id=201,
                                user=SimpleNamespace(id=7), db=db)
            assert ex.value.status_code == 403

            db.query(Order).filter(Order.external_id == "D").delete()
            db.commit()
            complete = catalog_metrics(days=30, account_id=101,
                                       user=SimpleNamespace(id=7), db=db)
            assert complete["orders_complete"] is True
    finally:
        engine.dispose()


def test_catalog_metrics_route_is_registered():
    from app.main import app
    assert "/api/v1/analytics/catalog-metrics" in app.openapi()["paths"]
