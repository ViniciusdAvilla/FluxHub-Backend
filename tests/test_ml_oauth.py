"""OAuth realista com httpx.MockTransport; nunca usa API/segredos de produção."""
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import settings
from app.connectors.mercado_livre import auth


@pytest.fixture
def ml_config(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr("test-jwt-key" * 5))
    monkeypatch.setattr(settings, "mercado_livre_client_id", SecretStr("123456"))
    monkeypatch.setattr(settings, "mercado_livre_client_secret", SecretStr("fake-only-secret"))
    monkeypatch.setattr(settings, "mercado_livre_redirect_uri",
                        "https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback")
    monkeypatch.setattr(settings, "mercado_livre_pkce_enabled", True)


def test_pkce_s256_matches_verifier_without_database_schema_change(ml_config):
    state = "a-valid-test-state-with-high-entropy-used-for-examples"
    url = auth.build_authorization_url(state)
    params = parse_qs(urlsplit(url).query)
    verifier = auth.pkce_code_verifier(state)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == [
        "https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback"
    ]
    assert 43 <= len(verifier) <= 128
    assert verifier != state
    assert auth.pkce_code_verifier(state + "different") != verifier
    assert "code_verifier" not in params


@pytest.mark.asyncio
async def test_token_exchange_sends_expected_pkce_verifier(ml_config, monkeypatch):
    state = "012345678901234567890123456789"
    def respond(request):
        assert str(request.url) == auth.TOKEN_URL
        assert request.method == "POST"
        assert "application/x-www-form-urlencoded" in request.headers["content-type"]
        fields = parse_qs(request.content.decode("utf-8"))
        assert fields["grant_type"] == ["authorization_code"]
        assert fields["code"] == ["test-code"]
        assert fields["code_verifier"] == [auth.pkce_code_verifier(state)]
        assert fields["redirect_uri"] == [settings.mercado_livre_redirect_uri]
        assert fields["client_secret"] == ["fake-only-secret"]
        return httpx.Response(200, json={
            "access_token": "fake-access", "refresh_token": "fake-refresh",
            "user_id": 12345, "expires_in": 21600,
        })

    original = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: original(transport=transport, **kwargs))
    tokens = await auth.exchange_authorization_code("test-code", state)
    assert tokens["user_id"] == 12345


@pytest.mark.asyncio
async def test_provider_error_is_diagnostic_and_never_leaks_secret(ml_config, monkeypatch, caplog):
    def respond(request):
        return httpx.Response(400, json={
            "error": "invalid_grant",
            "message": "LEAKED_CODE_OR_TOKEN_SHOULD_NEVER_BE_LOGGED",
        })

    original = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(auth.MercadoLivreOAuthError) as caught:
        await auth.exchange_authorization_code("private-oauth-code", "valid-state")
    assert caught.value.phase == "token_exchange"
    assert caught.value.http_status == 400
    assert caught.value.provider_error == "invalid_grant"
    assert "invalid_grant" in caplog.text
    assert "private-oauth-code" not in caplog.text
    assert "fake-only-secret" not in caplog.text
    assert "LEAKED_CODE_OR_TOKEN" not in caplog.text
    assert "LEAKED_CODE_OR_TOKEN" not in str(caught.value)


@pytest.mark.asyncio
async def test_non_pkce_app_uses_legacy_flow(ml_config, monkeypatch):
    monkeypatch.setattr(settings, "mercado_livre_pkce_enabled", False)
    url = auth.build_authorization_url("valid-state")
    assert "code_challenge" not in parse_qs(urlsplit(url).query)
    def respond(request):
        fields = parse_qs(request.content.decode("utf-8"))
        assert "code_verifier" not in fields
        return httpx.Response(200, json={
            "access_token": "test", "refresh_token": "test",
            "user_id": 900, "expires_in": 21600,
        })
    original = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: original(transport=transport, **kwargs))
    assert (await auth.exchange_authorization_code("code"))["user_id"] == 900


@pytest.mark.asyncio
async def test_pkce_requires_state(ml_config):
    with pytest.raises(auth.MercadoLivreOAuthError, match="state"):
        await auth.exchange_authorization_code("code")


@pytest.mark.asyncio
async def test_seller_api_403_is_logged_without_credentials(monkeypatch, caplog):
    from fastapi import HTTPException
    import app.routers.mercado_livre as ml

    async def fake_token(connection, db):
        return "secret-test-access-token"

    monkeypatch.setattr(ml, "get_valid_access_token", fake_token)
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, text="do-not-log-secret-response"))
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(HTTPException) as caught:
        await ml.seller_request(None, object(), "/orders/search", {"seller": 12345})
    assert caught.value.status_code == 502
    assert "resource=orders upstream_status=403" in caplog.text
    assert "secret-test-access-token" not in caplog.text
    assert "do-not-log-secret-response" not in caplog.text
    assert "/orders/search" not in caplog.text
