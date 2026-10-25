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
