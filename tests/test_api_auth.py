from fastapi import FastAPI, Depends, Header
from fastapi.testclient import TestClient
from app.config.settings import settings
from app.core.api_auth import protect_fastapi


def client():
    app = FastAPI()
    @app.get("/api/v1/health")
    def health():
        return {"status": "online"}
    @app.get("/api/v1/products/")
    def products(authorization: str | None = Header(default=None)):
        return {"authorization": authorization}
    @app.get("/api/v1/connectors/mercado-livre/callback")
    def callback(state: str):
        return {"state": state}
    protect_fastapi(app)
    return TestClient(app)


def test_health_public(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "")
    with client() as c:
        assert c.get("/api/v1/health").status_code == 200


def test_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "")
    with client() as c:
        assert c.get("/api/v1/products/").status_code == 503


def test_separate_tokens_and_compatibility(monkeypatch):
    token = "s" * 48
    monkeypatch.setattr(settings, "backend_token", token)
    with client() as c:
        assert c.get("/api/v1/products/").status_code == 401
        assert c.get("/api/v1/products/", headers={"X-Backend-Token": "invalid"}).status_code == 401
        r = c.get("/api/v1/products/", headers={
            "X-Backend-Token": token, "Authorization": "Bearer user.jwt.token"})
        assert r.status_code == 200
        assert r.json()["authorization"] == "Bearer user.jwt.token"
        assert c.get("/openapi.json", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert c.get("/api/v1/connectors/mercado-livre/callback?state=x").status_code == 200


def test_user_jwt_alone_not_a_service_token(monkeypatch):
    monkeypatch.setattr(settings, "backend_token", "s" * 48)
    with client() as c:
        assert c.get("/api/v1/products/", headers={
            "Authorization": "Bearer different-user-jwt"}).status_code == 401
