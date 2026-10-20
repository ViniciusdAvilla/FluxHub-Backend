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
    async def fake_exchange(code):
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
