"""SerpAPI proxy contract: authenticated route, safe output, no key leakage."""
import asyncio

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.config.settings import settings
from app.routers import market


def test_serpapi_provider_status(monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", None)
    assert market.providers(_user=None)["googleShopping"] is False
    monkeypatch.setattr(settings, "serpapi_key", SecretStr("test-only-secret"))
    assert market.providers(_user=None)["googleShopping"] is True


def test_serpapi_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(market.google_shopping(market.ShoppingQuery(query="pastilha freio"), _user=None))
    assert error.value.status_code == 503


def test_serpapi_returns_only_public_shopping_fields(monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", SecretStr("test-only-secret"))
    def respond(request: httpx.Request):
        assert request.url.host == "serpapi.com"
        assert request.url.params["engine"] == "google_shopping"
        assert request.url.params["q"] == "pastilha freio"
        assert request.url.params["gl"] == "br"
        assert request.url.params["hl"] == "pt-br"
        assert request.url.params["api_key"] == "test-only-secret"
        return httpx.Response(200, json={
            "search_metadata": {"status": "Success", "secret_data": "never return"},
            "shopping_results": [{
                "title": "Pastilha de freio", "source": "Loja exemplo",
                "extracted_price": 99.9, "product_link": "https://example.com/product",
                "private_field": "never return",
            }],
            "categorized_shopping_results": [{
                "shopping_results": [{
                    "title": "Pastilha premium", "source": "Outra loja",
                    "extracted_price": 129.9, "link": "https://example.com/premium",
                }],
            }],
        })
    original_client = httpx.AsyncClient
    monkeypatch.setattr(market.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), timeout=kwargs.get("timeout", 20.0),
    ))
    result = asyncio.run(market.google_shopping(
        market.ShoppingQuery(query="pastilha freio"), _user=None,
    ))
    assert len(result["shopping_results"]) == 2
    assert result["shopping_results"][0]["extracted_price"] == 99.9
    assert "private_field" not in result["shopping_results"][0]
    assert "search_metadata" not in result
    assert "test-only-secret" not in str(result)
