"""Test public ML profile fields and per-tenant account ownership without external APIs."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.connectors.mercado_livre.identity import safe_ml_image_url, normalized_seller_identity
from app.routers import mercado_livre as ml


def test_seller_identity_uses_brand_or_full_name_then_nickname():
    p = {"id": 818, "nickname": "LOJA808",
         "first_name": "Ana", "last_name": "Silva",
         "company": {"brand_name": "Minha Loja"},
         "logo": "http://mla-s2-p.mlstatic.com/portrait.jpg"}
    assert normalized_seller_identity(p, 818) == {
        "account_id": 818, "nickname": "LOJA808",
        "display_name": "Minha Loja",
        "picture_url": "https://mla-s2-p.mlstatic.com/portrait.jpg",
    }
    assert normalized_seller_identity({"nickname": "VENDEDOR"}, 818)["display_name"] == "VENDEDOR"
    assert normalized_seller_identity({"nickname": "V", "logo": None}, 818)["picture_url"] is None


@pytest.mark.parametrize("url", [
    "https://evil.example/abc", "https://mlstatic.com.attacker.invalid/image",
    "javascript:alert(1)", "data:image/svg+xml,<svg/>",
    "https://user:pass@mlstatic.com/p.png", "//mlstatic.com/p.png",
    "https://mlstatic.com:444/p.png", "http://localhost/secret",
])
def test_photo_url_rejects_non_provider_hosts(url):
    assert safe_ml_image_url(url) is None


@pytest.mark.asyncio
async def test_seller_endpoint_validates_tenant_account_and_only_returns_public_fields(monkeypatch):
    seller = SimpleNamespace(mercado_livre_user_id=817)
    owner = SimpleNamespace(id=17)
    seen = []

    def owned(db, owner_id, account_id):
        assert owner_id == 17
        if account_id != 817:
            raise HTTPException(404, "Conta não encontrada")
        return seller

    async def fake_request(db, account, path, params=None):
        seen.append(path)
        if path == "/users/me":
            return {"id": 817, "nickname": "MEUVENDEDOR",
                    "first_name": "Marina", "last_name": "Lima",
                    "email": "nao-expor@example.com", "logo": None,
                    "access_token": "NAO_EXIBIR"}
        if path == "/users/817":
            return {"id": 817, "logo": "https://http2.mlstatic.com/avatar.jpg"}
        raise AssertionError(path)

    monkeypatch.setattr(ml, "get_account", owned)
    monkeypatch.setattr(ml, "seller_request", fake_request)

    profile = await ml.seller_identity(account_id=817, user=owner, db=object())
    assert profile == {"account_id": 817, "nickname": "MEUVENDEDOR",
                       "display_name": "Marina Lima",
                       "picture_url": "https://http2.mlstatic.com/avatar.jpg"}
    assert seen == ["/users/me", "/users/817"]
    assert "email" not in profile and "access_token" not in profile
    with pytest.raises(HTTPException) as error:
        await ml.seller_identity(account_id=999, user=owner, db=object())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_optional_photo_error_does_not_interrupt_identity(monkeypatch):
    account = SimpleNamespace(mercado_livre_user_id=17)
    monkeypatch.setattr(ml, "get_account", lambda db, owner_id, account_id: account)

    async def fake_request(db, account, path, params=None):
        if path == "/users/me":
            return {"id": 17, "nickname": "VENDAS17"}
        raise HTTPException(502, "Imagem indisponível")

    monkeypatch.setattr(ml, "seller_request", fake_request)
    profile = await ml.seller_identity(account_id=17, user=SimpleNamespace(id=1), db=object())
    assert profile["display_name"] == "VENDAS17"
    assert profile["picture_url"] is None
