"""Security integration checks without external OAuth network calls."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import os

from app.config.settings import settings
from app.core.security import hash_password
from app.database.models import Base, UserModel, ProductModel, CompanyDataSourceModel
from app.database.session import get_db
from app.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "backend_token", "s" * 48)
    monkeypatch.setattr(settings, "sync_worker_enabled", False)
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr("j" * 48))
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(settings, "mercado_livre_client_id", SecretStr("123456"))
    monkeypatch.setattr(settings, "mercado_livre_client_secret", SecretStr("TEST_ONLY"))
    monkeypatch.setattr(settings, "mercado_pago_client_id", SecretStr("123456"))
    monkeypatch.setattr(settings, "mercado_pago_client_secret", SecretStr("TEST_ONLY"))
    with Session() as db:
        db.add(UserModel(email="admin@example.com", password_hash=hash_password("AdminPassw0rd!"),
                         is_admin=True, is_active=True))
        db.commit()
    def override():
        with Session() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c, Session
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def svc():
    return {"X-Backend-Token": "s" * 48}


def login(c, email, password):
    r = c.post("/api/v1/auth/login", headers=svc(),
               json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {**svc(), "Authorization": "Bearer " + r.json()["access_token"]}


def test_admin_user_lifecycle_and_isolation(client):
    c, Session = client
    assert c.get("/api/v1/admin/users").status_code == 401
    admin_headers = login(c, "admin@example.com", "AdminPassw0rd!")
    assert c.get("/api/v1/admin/overview", headers=admin_headers).json()["total_users"] == 1
    response = c.post("/api/v1/admin/users", headers=admin_headers, json={
        "email": "client1@example.com", "password": "ClientPassw0rd! "})
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    assert c.get(f"/api/v1/admin/users/{user_id}/overview",
                 headers=admin_headers).json()["user"]["id"] == user_id
    client_headers = login(c, "client1@example.com", "ClientPassw0rd! ")
    assert c.get("/api/v1/admin/users", headers=client_headers).status_code == 403
    assert c.get("/api/v1/auth/me", headers=client_headers).json()["id"] == user_id
    with Session() as db:
        db.add(CompanyDataSourceModel(
            company_id=user_id, provider="excel", account_key="excel",
            display_name="arquivo", connected=True, enabled=True))
        db.add(ProductModel(
            company_id=user_id, source_key="excel", external_id="SKU1", title="A",
            category="A", brand="B", original_price=10, promotional_price=9,
            discount_rate=10, stock_qty=5, sales_30d=0, rating=4,
            is_free_shipping=False, item_condition="new"))
        db.commit()
    assert len(c.get("/api/v1/products/", headers=client_headers).json()) == 1
    assert c.get("/api/v1/stock/", headers=client_headers).json()["total_stock"] == 5
    assert c.get("/api/v1/stock/", headers=admin_headers).json()["total_stock"] == 0
    assert c.get("/api/v1/indicators/", headers=admin_headers).json()["total_products"] == 0
    assert c.get("/api/v1/orders/", headers=client_headers).json() == []
    admin_products = c.get("/api/v1/products/", headers=admin_headers)
    assert admin_products.status_code == 200
    assert admin_products.json() == []
    changed = c.post(f"/api/v1/admin/users/{user_id}/reset-password",
                     headers=admin_headers, json={"password": "NewClientPassw0rd!"})
    assert changed.status_code == 200
    assert c.get("/api/v1/auth/me", headers=client_headers).status_code == 401
    client_headers = login(c, "client1@example.com", "NewClientPassw0rd!")
    disabled = c.patch(f"/api/v1/admin/users/{user_id}/status", headers=admin_headers,
                       json={"is_active": False})
    assert disabled.status_code == 200
    assert c.get("/api/v1/products/", headers=client_headers).status_code == 401
    assert c.post("/api/v1/auth/login", headers=svc(), json={
        "email": "client1@example.com", "password": "NewClientPassw0rd!"}).status_code == 401


def test_mercado_livre_state_bound_to_user_and_one_time(client, monkeypatch):
    c, Session = client
    admin_headers = login(c, "admin@example.com", "AdminPassw0rd!")
    async def fake_exchange(code, state=None):
        return {"access_token": "FAKE", "refresh_token": "REFRESH",
                "user_id": 34567, "expires_in": 3600}
    async def fake_profile(token):
        return {"id": 34567, "nickname": "Loja testes"}
    monkeypatch.setattr("app.routers.mercado_livre.exchange_authorization_code", fake_exchange)
    monkeypatch.setattr("app.routers.mercado_livre.fetch_profile", fake_profile)
    from urllib.parse import parse_qs, urlsplit
    result = c.get("/api/v1/connectors/mercado-livre/authorize",
                   headers=admin_headers)
    assert result.status_code == 200, result.text
    state = parse_qs(urlsplit(result.json()["authorization_url"]).query)["state"][0]
    callback = c.get("/api/v1/connectors/mercado-livre/callback",
                     params={"code": "fake-code", "state": state})
    assert callback.status_code == 200, callback.text
    assert callback.json()["account_id"] == 34567
    assert c.get("/api/v1/connectors/mercado-livre/callback",
                 params={"code": "fake-code", "state": state}).status_code == 400
    status = c.get("/api/v1/connectors/mercado-livre", headers=admin_headers)
    assert status.status_code == 200
    assert len(status.json()) == 1
    assert "access_token" not in status.text
    diag = c.get("/api/v1/connectors/mercado-livre/diagnostics", headers=admin_headers)
    assert diag.status_code == 200
    diagnostics = diag.json()
    assert diagnostics["connection_count"] == 1
    assert diagnostics["app_credentials_configured"] is True
    assert diagnostics["accounts"][0]["seller_id"] == 34567
    assert diagnostics["accounts"][0]["source_connected"] is True
    assert diagnostics["accounts"][0]["source_enabled"] is True
    assert "access_token" not in diag.text
    assert "refresh_token" not in diag.text
    assert "TEST_ONLY" not in diag.text


def test_mercado_pago_callback_and_account_isolation(client, monkeypatch):
    c, Session = client
    admin_headers = login(c, "admin@example.com", "AdminPassw0rd!")
    async def fake_oauth(body):
        return {"access_token": "FAKE_MP", "refresh_token": "REFRESH_MP",
                "user_id": "12345678", "expires_in": 3600}
    monkeypatch.setattr("app.routers.mercado_pago.oauth_token", fake_oauth)
    from urllib.parse import parse_qs, urlsplit
    result = c.get("/api/v1/connectors/mercado-pago/authorize",
                   headers=admin_headers)
    assert result.status_code == 200, result.text
    state = parse_qs(urlsplit(result.json()["authorization_url"]).query)["state"][0]
    callback = c.get("/api/v1/connectors/mercado-pago/callback",
                     params={"state": state, "code": "fake-code"})
    assert callback.status_code == 200, callback.text
    assert callback.json()["account_id"] == "12345678"
    assert c.get("/api/v1/connectors/mercado-pago", headers=admin_headers).json()[0]["connected"]



def test_sync_imports_real_listing_and_order_into_postgresql(client, monkeypatch):
    """O dashboard depende dos registros persistidos, não de rotas 200 vazias."""
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    from app.database.models import MercadoLivreConnectionModel, MercadoLivreOrderSnapshotModel
    from datetime import datetime, timezone
    from app.core.security import encrypt_token
    with Session() as db:
        admin = db.scalar(select(UserModel).where(UserModel.email == "admin@example.com"))
        db.add(MercadoLivreConnectionModel(
            user_id=admin.id, mercado_livre_user_id=34567,
            nickname="Loja de teste",
            access_token_encrypted=encrypt_token("fake-access"),
            refresh_token_encrypted=encrypt_token("fake-refresh"),
            expires_at=datetime.now(timezone.utc),
        ))
        db.add(CompanyDataSourceModel(
            company_id=admin.id, provider="mercado_livre",
            account_key="ml:34567", display_name="Loja de teste",
            enabled=True, connected=True,
        ))
        db.commit()

    calls = []
    async def fake_seller_request(db, connection, path, params=None):
        calls.append(path)
        if path.endswith("/items/search"):
            return {"results": ["MLB123456789"], "paging": {"total": 1}}
        if path == "/items/bulk":
            return [{"status_code": 200, "body": {
                "id": "MLB123456789", "seller_id": 34567,
                "title": "Produto real", "status": "active",
                "price": 49.90, "category_id": "MLB1234",
                "available_quantity": 12, "attributes": [],
            }}]
        if path == "/orders/search":
            return {"results": [{
                "id": 987654, "seller": {"id": 34567}, "status": "paid",
                "total_amount": 49.90, "date_created": "2026-10-20T12:00:00Z",
            }]}
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        raise AssertionError("Caminho inesperado: " + path)

    monkeypatch.setattr("app.routers.analytics.seller_request", fake_seller_request)
    result = c.post("/api/v1/analytics/sync",
                    params={"account_id": 34567, "limit": 20, "offset": 0},
                    headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["listings"] == 1
    assert result.json()["orders"] == 1
    assert result.json()["next_offset"] is None
    assert "/items/bulk" in calls
    products = c.get("/api/v1/products/", headers=headers).json()
    assert len(products) == 1
    assert products[0]["external_id"] == "MLB123456789"
    orders = c.get("/api/v1/orders/", headers=headers).json()
    assert len(orders) == 1
    assert str(orders[0]["external_id"]) == "987654"
    diagnostic = c.get("/api/v1/connectors/mercado-livre/diagnostics",
                       headers=headers).json()
    assert diagnostic["accounts"][0]["last_synced_at"] is not None

    from fastapi import HTTPException

    async def orders_unavailable(db, connection, path, params=None):
        if path == "/orders/search":
            raise HTTPException(502, "Mercado Livre indisponível.")
        return await fake_seller_request(db, connection, path, params)

    monkeypatch.setattr("app.routers.analytics.seller_request", orders_unavailable)
    partial = c.post("/api/v1/analytics/sync",
                     params={"account_id": 34567}, headers=headers)
    assert partial.status_code == 200, partial.text
    assert partial.json()["limit"] == 20
    assert partial.json()["listings"] == 1
    assert partial.json()["orders_unavailable"] is True


def test_ml_diagnostics_requires_user_authentication(client):
    c, _ = client
    assert c.get("/api/v1/connectors/mercado-livre/diagnostics",
                 headers=svc()).status_code == 401


def test_saas_marketplace_catalog_and_multi_tenant_oauth(client, monkeypatch):
    """Dois clientes SaaS com duas lojas distintas sem cruzar dados ou revelar tokens."""
    c, Session = client
    admin_headers = login(c, "admin@example.com", "AdminPassw0rd!")
    created = c.post("/api/v1/admin/users", headers=admin_headers, json={
        "email": "seller2@example.com", "password": "Seller2Passw0rd!",
    })
    assert created.status_code == 201, created.text
    seller2_headers = login(c, "seller2@example.com", "Seller2Passw0rd!")
    assert c.get("/api/v1/connectors/catalog").status_code == 401
    providers = c.get("/api/v1/connectors/catalog", headers=admin_headers)
    assert providers.status_code == 200
    listing = {p["id"]: p for p in providers.json()}
    assert listing["mercado_livre"]["status"] == "available"
    assert listing["mercado_livre"]["allows_multiple_accounts"] is True
    assert listing["mercado_pago"]["status"] == "planned"
    assert listing["shopee"]["status"] == "planned"
    assert listing["tiktok_shop"]["status"] == "planned"
    assert c.post("/api/v1/connectors/mercado-livre/authorize").status_code == 401

    async def exchange(code, state=None):
        account_id = 23456 if code == "second" else 12345
        return {"access_token": "FAKE", "refresh_token": "REFRESH",
                "user_id": account_id, "expires_in": 3600}

    async def profile(token):
        # Callback order below determines which seller the mocked profile represents.
        return {"id": seller_id[0], "nickname": f"Loja {seller_id[0]}"}

    seller_id = [12345]
    monkeypatch.setattr("app.routers.mercado_livre.exchange_authorization_code", exchange)
    monkeypatch.setattr("app.routers.mercado_livre.fetch_profile", profile)
    from urllib.parse import parse_qs, urlsplit

    for headers, code, sid in (
        (admin_headers, "first", 12345),
        (seller2_headers, "second", 23456),
    ):
        seller_id[0] = sid
        authorization = c.post("/api/v1/connectors/mercado-livre/authorize", headers=headers)
        assert authorization.status_code == 200, authorization.text
        state = parse_qs(urlsplit(
            authorization.json()["authorization_url"]).query)["state"][0]
        linked = c.get("/api/v1/connectors/mercado-livre/callback",
                       params={"state": state, "code": code})
        assert linked.status_code == 200, linked.text
        assert linked.json()["account_id"] == sid

    admin_accounts = c.get("/api/v1/connectors/mercado-livre", headers=admin_headers)
    other_accounts = c.get("/api/v1/connectors/mercado-livre", headers=seller2_headers)
    assert [a["account_id"] for a in admin_accounts.json()] == [12345]
    assert [a["account_id"] for a in other_accounts.json()] == [23456]
    assert "refresh_token" not in admin_accounts.text
    assert c.get("/api/v1/connectors/mercado-livre/profile",
                 headers=admin_headers, params={"account_id": 23456}).status_code == 404
    assert c.delete("/api/v1/connectors/mercado-livre",
                    headers=seller2_headers, params={"account_id": 12345}).status_code == 404


def test_sync_scans_more_than_1000_items_and_paginates_orders_separately(client, monkeypatch):
    """Scan has no 1000-item offset wall; 20-item bulk chunks are fully stored."""
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    from datetime import datetime, timezone
    from sqlalchemy import func
    from app.core.security import encrypt_token
    from app.database.models import (
        MercadoLivreConnectionModel, MercadoLivreOrderSnapshotModel,
    )

    with Session() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == "admin@example.com"))
        db.add(MercadoLivreConnectionModel(
            user_id=user.id, mercado_livre_user_id=34567, nickname="Loja teste",
            access_token_encrypted=encrypt_token("fake-access"),
            refresh_token_encrypted=encrypt_token("fake-refresh"),
            expires_at=datetime.now(timezone.utc),
        ))
        db.add(CompanyDataSourceModel(
            company_id=user.id, provider="mercado_livre", account_key="ml:34567",
            display_name="Loja teste", enabled=True, connected=True,
        ))
        db.commit()
        user_id = user.id

    total_items = 1025
    all_ids = [f"MLB{90000000 + i}" for i in range(total_items)]
    bulk_sizes = []

    async def fake_seller_request(db, connection, path, params=None):
        if path.endswith("/items/search"):
            assert params["search_type"] == "scan"
            assert "offset" not in params and "limit" not in params
            page = int(params.get("scroll_id", "cursor-0").split("-")[1])
            start = page * 100
            end = min(start + 100, total_items)
            return {
                "results": all_ids[start:end],
                "scroll_id": f"cursor-{page + 1}" if end < total_items else None,
            }
        if path == "/items/bulk":
            ids = params["ids"].split(",")
            bulk_sizes.append(len(ids))
            return [{"status_code": 200, "body": {
                "id": item_id, "seller_id": 34567,
                "title": "Produto teste", "status": "active", "price": 49.9,
                "category_id": "MLB1", "available_quantity": 1, "attributes": [],
            }} for item_id in ids]
        if path == "/orders/search":
            offset = params["offset"]
            return {
                "paging": {"total": 23},
                "results": [{
                    "id": 7000000 + i, "seller": {"id": 34567}, "status": "paid",
                    "total_amount": 10, "date_created": "2026-10-20T12:00:00Z",
                } for i in range(offset, min(offset + 20, 23))],
            }
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        raise AssertionError(path)

    monkeypatch.setattr("app.routers.analytics.seller_request", fake_seller_request)
    imported = 0
    cursor = None
    for _ in range(12):
        params = {"account_id": 34567, "phase": "listings"}
        if cursor:
            params["scroll_id"] = cursor
        result = c.post("/api/v1/analytics/sync", params=params, headers=headers)
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["orders"] == 0
        imported += payload["listings"]
        cursor = payload["next_scroll_id"]
        if cursor is None:
            break
    assert cursor is None
    assert imported == total_items
    assert max(bulk_sizes) == 20
    assert len(bulk_sizes) >= 52

    order_offset = 0
    order_count = 0
    for _ in range(3):
        result = c.post("/api/v1/analytics/sync", headers=headers, params={
            "account_id": 34567, "phase": "orders", "offset": order_offset,
        })
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["listings"] == 0
        order_count += payload["orders"]
        if payload["next_orders_offset"] is None:
            break
        order_offset = payload["next_orders_offset"]
    assert order_count == 23

    with Session() as db:
        products = db.scalar(select(func.count()).select_from(ProductModel).where(
            ProductModel.company_id == user_id,
            ProductModel.source_key == "ml:34567"))
        orders = db.scalar(select(func.count()).select_from(
            MercadoLivreOrderSnapshotModel).where(
                MercadoLivreOrderSnapshotModel.user_id == user_id,
                MercadoLivreOrderSnapshotModel.mercado_livre_user_id == 34567))
        assert products == total_items
        assert orders == 23

    # Re-importing a page never inserts duplicate products.
    repeated = c.post("/api/v1/analytics/sync", headers=headers, params={
        "account_id": 34567, "phase": "listings",
    })
    assert repeated.status_code == 200, repeated.text
    with Session() as db:
        products = db.scalar(select(func.count()).select_from(ProductModel).where(
            ProductModel.company_id == user_id,
            ProductModel.source_key == "ml:34567"))
        assert products == total_items



def test_sync_persists_every_listing_beyond_3000(client, monkeypatch):
    """More than 3000 seller IDs are scanned and every valid item is persisted."""
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    from datetime import datetime, timezone
    from sqlalchemy import func
    from app.core.security import encrypt_token
    from app.database.models import (
        MercadoLivreConnectionModel, MercadoLivreListingSnapshotModel,
    )

    with Session() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == "admin@example.com"))
        db.add(MercadoLivreConnectionModel(
            user_id=user.id, mercado_livre_user_id=34567,
            access_token_encrypted=encrypt_token("fake-access"),
            refresh_token_encrypted=encrypt_token("fake-refresh"),
            expires_at=datetime.now(timezone.utc),
        ))
        db.add(CompanyDataSourceModel(
            company_id=user.id, provider="mercado_livre", account_key="ml:34567",
            display_name="Conta de testes", enabled=True, connected=True))
        db.add_all([
            MercadoLivreListingSnapshotModel(
                user_id=user.id, mercado_livre_user_id=34567,
                external_id=f"MLB{i}", title="Anterior", status="active", price=10,
            ) for i in range(2999)
        ])
        db.add_all([
            ProductModel(
                company_id=user.id, source_key="ml:34567",
                external_id=f"MLB{i}", title="Anterior",
                category="Teste", brand="Marca", item_condition="new",
            ) for i in range(2999)
        ])
        db.commit()
        user_id = user.id

    bulk_ids = []

    async def fake_request(db, connection, path, params=None):
        if path.endswith("/items/search"):
            if params.get("scroll_id") == "second-page":
                return {"results": ["MLB3001", "MLB3002"], "scroll_id": None}
            return {"results": ["MLB2999", "MLB3000"], "scroll_id": "second-page"}
        if path == "/items/bulk":
            ids = params["ids"].split(",")
            bulk_ids.extend(ids)
            return [{"status_code": 200, "body": {
                "id": item_id, "seller_id": 34567, "title": "Novo",
                "status": "active", "price": 50, "available_quantity": 5,
                "attributes": [],
            }} for item_id in ids]
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        raise AssertionError(path)

    monkeypatch.setattr("app.routers.analytics.seller_request", fake_request)
    first = c.post("/api/v1/analytics/sync", headers=headers, params={
        "phase": "listings", "account_id": 34567,
    })
    assert first.status_code == 200, first.text
    assert first.json()["listings"] == 2
    assert first.json()["saved_listings"] == 2
    assert first.json()["storage_limit"] is None
    assert first.json()["next_scroll_id"] == "second-page"
    second = c.post("/api/v1/analytics/sync", headers=headers, params={
        "phase": "listings", "account_id": 34567, "scroll_id": "second-page",
    })
    assert second.status_code == 200, second.text
    assert second.json()["listings"] == 2
    assert second.json()["saved_listings"] == 2
    assert second.json()["next_scroll_id"] is None
    assert bulk_ids == ["MLB2999", "MLB3000", "MLB3001", "MLB3002"]

    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ProductModel).where(
            ProductModel.company_id == user_id,
            ProductModel.source_key == "ml:34567")) == 3003
        assert db.scalar(select(func.count()).select_from(
            MercadoLivreListingSnapshotModel).where(
                MercadoLivreListingSnapshotModel.user_id == user_id,
                MercadoLivreListingSnapshotModel.mercado_livre_user_id == 34567)) == 3003

    monkeypatch.setattr("app.routers.mercado_livre.seller_request", fake_request)
    live = c.get("/api/v1/connectors/mercado-livre/items/live",
                 params={"account_id": 34567}, headers=headers)
    assert live.status_code == 200, live.text
    assert len(live.json()["items"]) == 2
    assert live.json()["source"] == "mercado_livre_live"



def test_listing_scan_beyond_10000_preserves_page_on_rate_limit(client, monkeypatch):
    """A 429 after 10k IDs is retryable without losing or capping data."""
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from sqlalchemy import func
    from app.core.security import encrypt_token
    from app.database.models import MercadoLivreConnectionModel, MercadoLivreListingSnapshotModel
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    with Session() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == "admin@example.com"))
        db.add(MercadoLivreConnectionModel(
            user_id=user.id, mercado_livre_user_id=34567,
            access_token_encrypted=encrypt_token("fake-access"),
            refresh_token_encrypted=encrypt_token("fake-refresh"),
            expires_at=datetime.now(timezone.utc),
        ))
        db.add(CompanyDataSourceModel(
            company_id=user.id, provider="mercado_livre", account_key="ml:34567",
            display_name="Conta teste", enabled=True, connected=True))
        db.commit()
        user_id = user.id

    item_ids = [f"MLB{70000000 + i}" for i in range(10037)]
    bulk_ids = []
    rejected_once = False

    async def fake_seller_request(db, connection, path, params=None):
        nonlocal rejected_once
        if path.endswith("/items/search"):
            page = int(params.get("scroll_id", "0"))
            if page == 100 and not rejected_once:
                rejected_once = True
                raise HTTPException(429, "Muitas consultas", headers={"Retry-After": "1"})
            start = page * 100
            end = min(start + 100, len(item_ids))
            return {
                "results": item_ids[start:end],
                "scroll_id": str(page + 1) if end < len(item_ids) else None,
            }
        if path == "/items/bulk":
            ids = params["ids"].split(",")
            bulk_ids.extend(ids)
            return [{"status_code": 200, "body": {
                "id": item_id, "seller_id": 34567, "title": "Teste",
                "status": "active", "price": 10, "attributes": [],
            }} for item_id in ids]
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        raise AssertionError(path)

    monkeypatch.setattr("app.routers.analytics.seller_request", fake_seller_request)
    cursor = None
    total_scanned = 0
    limited = 0
    for _ in range(104):
        params = {"phase": "listings", "account_id": 34567}
        if cursor is not None:
            params["scroll_id"] = cursor
        response = c.post("/api/v1/analytics/sync", headers=headers, params=params)
        if response.status_code == 429:
            limited += 1
            assert response.headers["retry-after"] == "1"
            continue  # same cursor, no page silently lost
        assert response.status_code == 200, response.text
        payload = response.json()
        total_scanned += payload["listings"]
        cursor = payload["next_scroll_id"]
        if cursor is None:
            break
    assert limited == 1
    assert total_scanned == len(item_ids)
    assert len(bulk_ids) == len(item_ids)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ProductModel).where(
            ProductModel.company_id == user_id,
            ProductModel.source_key == "ml:34567")) == len(item_ids)
        assert db.scalar(select(func.count()).select_from(
            MercadoLivreListingSnapshotModel).where(
                MercadoLivreListingSnapshotModel.user_id == user_id,
                MercadoLivreListingSnapshotModel.mercado_livre_user_id == 34567)) == len(item_ids)


def test_background_job_runs_after_browser_request_and_records_progress(client, monkeypatch):
    """Durable job checkpoints advance from scan into partitioned orders."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from app.core.security import encrypt_token
    from app.database.models import (
        MercadoLivreConnectionModel, MercadoLivreSyncJobModel,
        MercadoLivreListingSnapshotModel, MercadoLivreOrderSnapshotModel,
    )
    from app.services import ml_sync_worker as worker

    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    with Session() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == "admin@example.com"))
        db.add(MercadoLivreConnectionModel(
            user_id=user.id, mercado_livre_user_id=34567,
            access_token_encrypted=encrypt_token("fake-access"),
            refresh_token_encrypted=encrypt_token("fake-refresh"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.add(CompanyDataSourceModel(company_id=user.id, provider="mercado_livre",
            account_key="ml:34567", display_name="Conta de teste", enabled=True, connected=True))
        db.commit()
        user_id = user.id

    created = c.post("/api/v1/analytics/sync-jobs", headers=headers,
                     params={"account_id": 34567})
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"
    assert c.get("/api/v1/analytics/sync-jobs/34567", headers=headers).json()["status"] == "queued"

    # Simulate a worker killed after acquiring a scan cursor. Expired leases
    # must safely replay the scan instead of skipping an uncommitted page.
    with Session() as db:
        job = db.scalar(select(MercadoLivreSyncJobModel))
        job.status = "running"
        job.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        job.scroll_id = "expired-cursor"
        job.scroll_updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        job.listings_scanned = 40
        db.commit()

    target_order_date = datetime.now(timezone.utc) - timedelta(days=10)
    async def fake_request(db, account, path, params=None):
        if path.endswith("/items/search"):
            return {"results": ["MLB12345"], "scroll_id": None,
                    "paging": {"total": 1}}
        if path == "/items/bulk":
            return [{"status_code": 200, "body": {"id": "MLB12345", "seller_id": 34567,
                    "title": "Anúncio", "status": "active", "price": 20, "attributes": []}}]
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        if path == "/orders/search":
            lower = datetime.fromisoformat(params["order.date_created.from"])
            upper = datetime.fromisoformat(params["order.date_created.to"])
            included = lower <= target_order_date <= upper
            return {"results": ([{"id": 12345, "seller": {"id": 34567},
                "status": "paid", "total_amount": 20,
                "date_created": target_order_date.isoformat()}] if included else []),
                "paging": {"total": int(included)}}
        raise AssertionError(path)

    monkeypatch.setattr("app.routers.analytics.seller_request", fake_request)
    monkeypatch.setattr(worker, "SessionLocal", Session)
    for _ in range(24):
        assert asyncio.run(worker.process_one_sync_batch()) is True
        status = c.get("/api/v1/analytics/sync-jobs/34567", headers=headers).json()
        if status["status"] == "completed":
            break
    assert status["status"] == "completed", status
    assert status["listings_scanned"] == 1
    assert status["listings_saved"] == 1
    assert status["listings_total"] == 1
    assert status["orders_saved"] == 1
    assert status["pages_done"] >= 2
    with Session() as db:
        assert db.scalar(select(MercadoLivreSyncJobModel).where(
            MercadoLivreSyncJobModel.user_id == user_id)).status == "completed"
        assert len(db.scalars(select(MercadoLivreListingSnapshotModel)).all()) == 1
        assert len(db.scalars(select(MercadoLivreOrderSnapshotModel)).all()) == 1



def _connected_ml_account(Session, seller_id=34567):
    from datetime import datetime, timedelta, timezone
    from app.core.security import encrypt_token
    from app.database.models import MercadoLivreConnectionModel
    with Session() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == "admin@example.com"))
        db.add(MercadoLivreConnectionModel(
            user_id=user.id, mercado_livre_user_id=seller_id,
            access_token_encrypted=encrypt_token("fake-access"),
            refresh_token_encrypted=encrypt_token("fake-refresh"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.add(CompanyDataSourceModel(
            company_id=user.id, provider="mercado_livre",
            account_key=f"ml:{seller_id}", display_name="Seller",
            enabled=True, connected=True,
        ))
        db.commit()
        return user.id


def test_sync_pause_is_durable_and_resume_preserves_checkpoint(client, monkeypatch):
    import asyncio
    from app.database.models import MercadoLivreSyncJobModel
    from app.services import ml_sync_worker as worker

    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    _connected_ml_account(Session)
    created = c.post("/api/v1/analytics/sync-jobs",
                     headers=headers, params={"account_id": 34567})
    assert created.status_code == 202
    with Session() as db:
        job = db.scalar(select(MercadoLivreSyncJobModel))
        job.listings_saved = 123
        job.listings_scanned = 123
        job.pending_listing_ids = ["MLB10001", "MLB10002"]
        job.pending_next_scroll_id = "cursor-next"
        db.commit()

    paused = c.post("/api/v1/analytics/sync-jobs/34567/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["pending_listings_count"] == 2
    monkeypatch.setattr(worker, "SessionLocal", Session)
    assert asyncio.run(worker.process_one_sync_batch()) is False
    resumed = c.post("/api/v1/analytics/sync-jobs",
                     headers=headers, params={"account_id": 34567})
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"
    assert resumed.json()["listings_saved"] == 123
    with Session() as db:
        job = db.scalar(select(MercadoLivreSyncJobModel))
        assert job.pending_listing_ids == ["MLB10001", "MLB10002"]


def test_staged_scan_retries_bulk_without_scanning_again(client, monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from app.database.models import MercadoLivreSyncJobModel
    from app.services import ml_sync_worker as worker

    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    _connected_ml_account(Session)
    assert c.post("/api/v1/analytics/sync-jobs", headers=headers,
                  params={"account_id": 34567}).status_code == 202
    scans = []
    bulk_attempts = []
    async def fake_request(db, account, path, params=None):
        if path.endswith("/items/search"):
            scans.append(path)
            return {"results": ["MLB11111"], "scroll_id": None,
                    "paging": {"total": 1}}
        if path == "/items/bulk":
            bulk_attempts.append(path)
            if len(bulk_attempts) == 1:
                from fastapi import HTTPException
                raise HTTPException(502, "Detalhes indisponíveis")
            return [{"status_code": 200, "body": {
                "id": "MLB11111", "seller_id": 34567, "title": "Item",
                "status": "active", "price": 10, "attributes": [],
            }}]
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        raise AssertionError(path)
    monkeypatch.setattr("app.routers.analytics.seller_request", fake_request)
    monkeypatch.setattr(worker, "SessionLocal", Session)
    assert asyncio.run(worker.process_one_sync_batch()) is True
    with Session() as db:
        job = db.scalar(select(MercadoLivreSyncJobModel))
        assert job.status == "retry"
        assert job.pending_listing_ids == ["MLB11111"]
        assert job.listings_scanned == 0
        job.retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert asyncio.run(worker.process_one_sync_batch()) is True
    assert len(scans) == 1
    with Session() as db:
        job = db.scalar(select(MercadoLivreSyncJobModel))
        assert job.pending_listing_ids is None
        assert job.listings_saved == 1
        assert job.phase == "orders"


def test_revenue_uses_paid_orders_not_listing_prices(client):
    from datetime import datetime, timezone
    from app.database.models import (
        MercadoLivreSyncJobModel, MercadoLivreOrderSnapshotModel,
    )
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    user_id = _connected_ml_account(Session)
    with Session() as db:
        for external_id, status, amount in [
            ("1", "paid", 120.0), ("2", "payment_required", 450.0),
            ("3", "cancelled", 70.0),
        ]:
            db.add(MercadoLivreOrderSnapshotModel(
                user_id=user_id, mercado_livre_user_id=34567,
                external_id=external_id, status=status, total_amount=amount,
                created_at=datetime.now(timezone.utc), is_cancelled=status == "cancelled",
            ))
        db.commit()
    partial = c.get("/api/v1/sales/?days=30", headers=headers)
    assert partial.status_code == 200
    assert partial.json()["partial_data"] is True
    assert partial.json()["paid_order_count"] == 1
    assert partial.json()["gross_paid_order_amount"] == 120.0

    orders_job = c.post("/api/v1/analytics/sync-jobs/34567/orders", headers=headers)
    assert orders_job.status_code == 202
    assert orders_job.json()["phase"] == "orders"
    with Session() as db:
        job = db.scalar(select(MercadoLivreSyncJobModel))
        job.phase = "done"
        job.status = "completed"
        db.commit()
    complete = c.get("/api/v1/sales/?days=30", headers=headers)
    assert complete.json()["data_complete"] is True
    assert complete.json()["gross_paid_order_amount"] == 120.0



def test_sales_works_before_additive_sync_job_columns_are_migrated(client):
    """Revenue must stay readable even if the background worker migration is pending."""
    from datetime import datetime, timezone
    from app.database.models import (
        MercadoLivreSyncJobModel, MercadoLivreOrderSnapshotModel,
    )
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    user_id = _connected_ml_account(Session)
    with Session() as db:
        db.add(MercadoLivreSyncJobModel(
            user_id=user_id, mercado_livre_user_id=34567,
            phase="done", status="completed",
        ))
        db.add(MercadoLivreOrderSnapshotModel(
            user_id=user_id, mercado_livre_user_id=34567,
            external_id="paid-regression", status="paid",
            total_amount=55.50, is_cancelled=False,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        # Reproduce a real VPS deployment where the new columns are absent.
        db.execute(text(
            "ALTER TABLE mercado_livre_sync_jobs DROP COLUMN pending_listing_ids"
        ))
        db.execute(text(
            "ALTER TABLE mercado_livre_sync_jobs DROP COLUMN pending_next_scroll_id"
        ))
        db.commit()
    response = c.get("/api/v1/sales/?days=30&account_id=34567", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["gross_paid_order_amount"] == 55.50
    assert response.json()["data_complete"] is True


def test_complete_catalog_retry_prioritizes_orders_without_deleting_listings(client, monkeypatch):
    """After an upstream bulk failure, already-complete listings cannot block orders."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func
    from app.database.models import (
        MercadoLivreSyncJobModel as Job,
        MercadoLivreListingSnapshotModel as Listing,
        MercadoLivreConnectionModel as Connection,
    )
    from app.services import ml_sync_worker as worker

    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    user_id = _connected_ml_account(Session)
    assert c.post("/api/v1/analytics/sync-jobs", headers=headers,
                  params={"account_id": 34567}).status_code == 202
    with Session() as db:
        db.add(Listing(user_id=user_id, mercado_livre_user_id=34567,
                       external_id="MLBALREADY", title="Salvo",
                       status="active", price=100))
        job = db.scalar(select(Job).where(Job.user_id == user_id))
        job.status = "retry"
        job.retry_count = 2
        job.retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        job.listings_total = 1
        job.listings_saved = 1
        job.pending_listing_ids = ["MLBUNAVAILABLE"]
        job.pending_next_scroll_id = "expired-cursor"
        db.commit()

    monkeypatch.setattr(worker, "SessionLocal", Session)
    claimed = worker._claim_job()
    assert claimed is not None
    with Session() as db:
        job = db.get(Job, claimed)
        assert job.phase == "orders"
        assert job.status == "running"
        assert job.listings_saved == 1
        assert job.pending_listing_ids is None
        assert job.retry_count == 0
        assert db.scalar(select(func.count(Listing.id)).where(
            Listing.user_id == user_id)) == 1
        assert db.scalar(select(func.count(Connection.id)).where(
            Connection.user_id == user_id)) == 1


def test_orders_only_can_override_waiting_listing_retry(client):
    """A waiting 502 retry must not hide or block the orders/faturamento action."""
    from datetime import datetime, timedelta, timezone
    from app.database.models import MercadoLivreSyncJobModel as Job

    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    _connected_ml_account(Session)
    assert c.post("/api/v1/analytics/sync-jobs", headers=headers,
                  params={"account_id": 34567}).status_code == 202
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "retry"
        job.phase = "listings"
        job.retry_count = 3
        job.retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        job.listings_saved = 11517
        job.listings_total = 11517
        db.commit()
    response = c.post("/api/v1/analytics/sync-jobs/34567/orders", headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["phase"] == "orders"
    assert response.json()["listings_saved"] == 11517
    assert response.json()["retry_count"] == 0


def test_listing_batch_skips_permanently_unavailable_item_without_losing_page(client, monkeypatch):
    """One removed item (HTTP 404 in /items/bulk) must not block seller orders."""
    from sqlalchemy import func
    c, Session = client
    headers = login(c, "admin@example.com", "AdminPassw0rd!")
    user_id = _connected_ml_account(Session)
    async def fake_request(db, account, path, params=None):
        if path.endswith("/items/search"):
            return {"results": ["MLB98765001", "MLB98765002"],
                    "scroll_id": None, "paging": {"total": 2}}
        if path == "/items/bulk":
            return [
                {"status_code": 200, "body": {
                    "id": "MLB98765001", "seller_id": 34567, "title": "Ativo",
                    "status": "active", "price": 19.90, "attributes": []}},
                {"id": "MLB98765002", "status_code": 404, "body": {"message": "not found"}},
            ]
        if path.endswith("/items_visits/time_window"):
            return {"results": []}
        raise AssertionError(f"Request unexpectedly made: {path}")
    monkeypatch.setattr("app.routers.analytics.seller_request", fake_request)
    response = c.post("/api/v1/analytics/sync", headers=headers,
                      params={"account_id": 34567, "phase": "listings"})
    assert response.status_code == 200, response.text
    assert response.json()["saved_listings"] == 1
    assert response.json()["unavailable_listings"] == 1
    assert response.json()["listings_complete"] is True
    with Session() as db:
        from app.database.models import MercadoLivreListingSnapshotModel as Listing
        assert db.scalar(select(func.count(Listing.id)).where(
            Listing.user_id == user_id,
            Listing.mercado_livre_user_id == 34567)) == 1
