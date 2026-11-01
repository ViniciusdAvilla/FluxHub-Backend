"""OAuth/MCP tenant isolation and authorization-code lifecycle."""
import base64
import hashlib

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.models import (
    UserModel, ProductModel, MercadoLivreOrderSnapshotModel as Order,
    MercadoLivreOrderLineModel as Line, MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreAdsDailySnapshotModel as Ads,
)
from app.database.mcp_models import McpClient, McpAuthorizationRequest, McpGrant
from app.routers import mcp_oauth
from app.services.claude_tools import execute_tool, TOOL_DEFINITIONS


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr("m" * 48))
    monkeypatch.setattr(settings, "frontend_url", "https://myfluxhub.tech")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (UserModel.__table__, ProductModel.__table__, Order.__table__,
                  Line.__table__, Listing.__table__, Ads.__table__,
                  McpClient.__table__, McpAuthorizationRequest.__table__, McpGrant.__table__):
        table.create(bind=engine, checkfirst=True)
    with Session(engine) as session:
        session.add_all([
            UserModel(id=1, email="seller1@example.org", password_hash="hash", is_active=True),
            UserModel(id=2, email="seller2@example.org", password_hash="hash", is_active=True),
        ])
        session.commit()
        yield session
    engine.dispose()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_oauth_requires_pkce_and_is_revocable(db):
    uri = "https://claude.ai/api/mcp/oauth/callback"
    client = mcp_oauth.register_client(mcp_oauth.ClientInput(
        client_name="Claude", redirect_uris=[uri]), db=db)
    verifier = "a" * 48
    pending = mcp_oauth.authorize(
        client_id=client["client_id"], redirect_uri=uri, response_type="code",
        code_challenge=_challenge(verifier), code_challenge_method="S256",
        state="state123", scope="fluxhub:read", resource=mcp_oauth.resource_url(),
        db=db,
    )
    assert "myfluxhub.tech/mcp/autorizar?" in pending["consent_url"]
    request_id = pending["consent_url"].split("request_id=")[1]
    with pytest.raises(HTTPException):
        mcp_oauth.describe_request("no-such-request", user=db.get(UserModel, 1), db=db)
    assert mcp_oauth.describe_request(request_id, user=db.get(UserModel, 1), db=db)["client_name"] == "Claude"
    approval = mcp_oauth.approve(
        mcp_oauth.ApprovalInput(request_id=request_id),
        user=db.get(UserModel, 1), db=db,
    )
    code = approval["redirect_to"].split("code=")[1].split("&")[0]
    assert "state=state123" in approval["redirect_to"]
    with pytest.raises(HTTPException):
        mcp_oauth.exchange(
            grant_type="authorization_code", client_id=client["client_id"],
            code=code, redirect_uri=uri, code_verifier="wrong" * 10,
            refresh_token="", db=db,
        )
    token = mcp_oauth.exchange(
        grant_type="authorization_code", client_id=client["client_id"],
        code=code, redirect_uri=uri, code_verifier=verifier,
        refresh_token="", db=db,
    )
    assert token["token_type"] == "Bearer"
    assert mcp_oauth.authorized_user(db, token["access_token"]).id == 1
    with pytest.raises(HTTPException):
        mcp_oauth.exchange(
            grant_type="authorization_code", client_id=client["client_id"],
            code=code, redirect_uri=uri, code_verifier=verifier, refresh_token="", db=db,
        )
    refreshed = mcp_oauth.exchange(
        grant_type="refresh_token", client_id=client["client_id"],
        code="", redirect_uri="", code_verifier="",
        refresh_token=token["refresh_token"], db=db,
    )
    assert refreshed["refresh_token"] != token["refresh_token"]
    assert mcp_oauth.connection_status(user=db.get(UserModel, 1), db=db)["connected_clients"] == 1
    mcp_oauth.revoke_all(user=db.get(UserModel, 1), db=db)
    with pytest.raises(HTTPException):
        mcp_oauth.authorized_user(db, refreshed["access_token"])


def test_redirect_validation_rejects_insecure_external_clients():
    assert mcp_oauth._redirect_valid("https://claude.ai/callback")
    assert not mcp_oauth._redirect_valid("http://external.example/callback")
    assert not mcp_oauth._redirect_valid("https://someone:password@host.example/callback")
    assert not mcp_oauth._redirect_valid("https://claude.ai/callback#fragment")


def test_tools_are_scoped_and_read_only(db):
    db.add_all([
        ProductModel(id=1, company_id=1, external_id="ML1", title="Produto A",
                     category="Geral", brand="Marca", item_condition="new", stock_qty=0),
        ProductModel(id=2, company_id=2, external_id="ML2", title="Produto secreto",
                     category="Geral", brand="Marca", item_condition="new", stock_qty=0),
    ])
    db.commit()
    allowed = {item["name"] for item in TOOL_DEFINITIONS}
    assert "resumo_operacao" in allowed and "consultar_estoque" in allowed
    assert execute_tool(db, 1, "resumo_operacao")["produtos_catalogo"] == 1
    result = execute_tool(db, 1, "consultar_estoque")
    assert [item["nome"] for item in result["produtos"]] == ["Produto A"]
    with pytest.raises(ValueError):
        execute_tool(db, 1, "consultar_estoque", {"limite": 10000})
    with pytest.raises(ValueError):
        execute_tool(db, 1, "excluir_pedidos", {})


def test_mcp_jsonrpc_protocol_requires_mcp_token(db):
    import asyncio
    import json
    from starlette.requests import Request
    from app.routers.fluxhub_mcp import mcp_handler

    def make_request(headers: dict, data: dict):
        body = json.dumps(data).encode()
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/mcp",
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]},
                       receive=receive)

    response = asyncio.run(mcp_handler(make_request(
        {"content-type": "application/json"}, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        db=db))
    assert response.status_code == 401
    client = mcp_oauth.register_client(mcp_oauth.ClientInput(
        client_name="Test Claude", redirect_uris=["https://claude.ai/callback"]), db=db)
    grant = McpGrant(id="test_grant", client_id=client["client_id"], user_id=1,
                     refresh_hash="f" * 64, token_version=0,
                     expires_at=mcp_oauth.now_utc() + __import__("datetime").timedelta(days=2))
    db.add(grant)
    db.commit()
    access = mcp_oauth._sign({
        "iss": "https://myfluxhub.tech", "aud": "https://myfluxhub.tech/mcp",
        "type": "mcp", "sub": "1", "gid": grant.id, "ver": 0,
        "exp": int(__import__("time").time()) + 3600,
    })
    result = asyncio.run(mcp_handler(make_request(
        {"content-type": "application/json", "authorization": "Bearer " + access},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}), db=db))
    assert result.status_code == 200
    output = json.loads(result.body)
    assert any(x["name"] == "consultar_vendas" for x in output["result"]["tools"])


def test_password_session_version_change_revokes_old_mcp_grant(db):
    client = mcp_oauth.register_client(mcp_oauth.ClientInput(
        client_name="Claude", redirect_uris=["https://claude.ai/callback"]), db=db)
    valid_refresh_token = "valid_refresh_token_for_session_revocation_test"
    grant = McpGrant(
        id="grant_revocation", client_id=client["client_id"], user_id=1,
        refresh_hash=mcp_oauth.digest(valid_refresh_token), token_version=0,
        expires_at=mcp_oauth.now_utc() + __import__("datetime").timedelta(days=1),
    )
    db.add(grant)
    db.commit()
    access = mcp_oauth._sign({
        "iss": "https://myfluxhub.tech", "aud": mcp_oauth.resource_url(),
        "type": "mcp", "sub": "1", "ver": 0, "gid": grant.id,
        "exp": int(__import__("time").time()) + 3600,
    })
    assert mcp_oauth.authorized_user(db, access).id == 1
    user = db.get(UserModel, 1)
    user.token_version = 1
    db.commit()
    with pytest.raises(HTTPException):
        mcp_oauth.authorized_user(db, access)
    with pytest.raises(HTTPException):
        mcp_oauth.exchange(
            grant_type="refresh_token", client_id=client["client_id"],
            code="", redirect_uri="", code_verifier="",
            refresh_token=valid_refresh_token, db=db,
        )
