"""Category captions use the official ID lookup and never expose an OAuth token."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import analytics


@pytest.mark.parametrize("value", [
    "", "../api", "MLB111,MLB222,MLB333,MLB444,MLB555,MLB666",
    "MLB12", "MLB123?access_token=abc", "MLB123,,MLB456",
])
def test_category_identifiers_are_strictly_bounded(value):
    with pytest.raises(HTTPException) as error:
        analytics._validated_category_ids(value)
    assert error.value.status_code == 422


def test_duplicate_category_ids_are_removed():
    assert analytics._validated_category_ids("MLB63517,MLB63517,MLB22709") == [
        "MLB63517", "MLB22709"
    ]


@pytest.mark.asyncio
async def test_category_names_use_only_an_owned_account_and_server_side_token(monkeypatch):
    analytics._CATEGORY_CACHE.clear()
    seen = []

    class SessionStub:
        def scalar(self, statement):
            return SimpleNamespace(id=7, user_id=13)

    async def fake_token(connection, db):
        assert connection.user_id == 13
        return "SENSITIVE_DO_NOT_RETURN"

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"id": "MLB63517", "name": "Peças para Veículos"}

    class ClientStub:
        def __init__(self, timeout):
            assert timeout <= 4
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, headers):
            seen.append((url, headers))
            return FakeResponse()

    monkeypatch.setattr(analytics, "get_valid_access_token", fake_token)
    monkeypatch.setattr(analytics.httpx, "AsyncClient", ClientStub)
    result = await analytics.category_names(
        "MLB63517", SimpleNamespace(id=13), SessionStub()
    )
    assert result == {"MLB63517": "Peças para Veículos"}
    assert len(seen) == 1
    assert seen[0][0].endswith("/categories/MLB63517")
    assert "SENSITIVE" not in str(result)
