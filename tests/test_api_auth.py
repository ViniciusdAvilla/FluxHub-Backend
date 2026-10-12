"""Testa isolamento da API sem acessar o banco de produtos."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.api_auth import protect_fastapi


def make_client():
    app = FastAPI()

    @app.get("/api/v1/health")
    def health():
        return {"status": "online"}

    @app.get("/api/v1/products/")
    def products():
        return [{"id": "item-1"}]

    protect_fastapi(app)
    return TestClient(app)


def test_health_public_even_without_token(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "")
    with make_client() as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "online"}


def test_fail_closed_without_config(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "")
    with make_client() as client:
        for path in ("/api/v1/products/", "/docs", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.headers["cache-control"] == "no-store"


def test_rejects_short_or_invalid_secret(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "dev")
    with make_client() as client:
        assert client.get("/api/v1/products/", headers={"Authorization": "Bearer dev"}).status_code == 503


def test_requires_bearer_and_rejects_bad_credentials(monkeypatch):
    token = "t" * 48
    monkeypatch.setattr(settings, "backend_token", token)
    with make_client() as client:
        for authorization in ("", "Basic " + token, "Bearer " + "X" * 48):
            headers = {"Authorization": authorization} if authorization else {}
            response = client.get("/api/v1/products/", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.headers["cache-control"] == "no-store"


def test_valid_bearer_unlocks_products_swagger_and_openapi(monkeypatch):
    token = "t" * 48
    monkeypatch.setattr(settings, "backend_token", token)
    headers = {"Authorization": f"Bearer {token}"}
    with make_client() as client:
        product = client.get("/api/v1/products/", headers=headers)
        assert product.status_code == 200
        assert product.json() == [{"id": "item-1"}]
        assert product.headers["cache-control"] == "no-store"
        assert client.get("/docs", headers=headers).status_code == 200
        response = client.get("/openapi.json", headers=headers)
        assert response.status_code == 200
        assert "/api/v1/products/" in response.json()["paths"]


def test_rejects_unrecognized_paths_too(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "t" * 48)
    with make_client() as client:
        assert client.get("/other").status_code == 401
