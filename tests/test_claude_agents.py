"""Encrypted Claude API keys are isolated by FluxHub user."""
import asyncio

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.models import ClaudeProviderConfigModel, UserModel
from app.routers import claude_agents


@pytest.fixture
def claude_db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ClaudeProviderConfigModel.__table__.create(bind=engine)
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    async def accept_test(key: str, model: str):
        assert key.startswith("sk-ant-")
        assert model in claude_agents.MODELS

    monkeypatch.setattr(claude_agents, "_check_anthropic", accept_test)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _user(user_id: int) -> UserModel:
    return UserModel(
        id=user_id, email=f"user{user_id}@example.com",
        password_hash="unused", is_active=True, is_admin=False,
    )


def _save(session: Session, user_id: int, key: str | None, model="claude-sonnet-5"):
    return asyncio.run(claude_agents.save_config(
        claude_agents.ClaudeConfigInput(api_key=key, model=model),
        user=_user(user_id), db=session,
    ))


def test_regular_user_saves_encrypted_key_and_never_exposes_it(claude_db):
    key = "sk-ant-" + "a" * 40
    assert _save(claude_db, 1, key)["configured"] is True
    encrypted = claude_db.get(ClaudeProviderConfigModel, 1).api_key_encrypted
    assert encrypted != key
    assert key not in encrypted
    status = claude_agents.agent_status(user=_user(1), db=claude_db)
    assert status["configured"] is True
    assert status["can_manage"] is True
    assert key not in str(status)


def test_user_keys_are_strictly_separated(claude_db):
    key1, key2 = "sk-ant-" + "b" * 40, "sk-ant-" + "c" * 40
    _save(claude_db, 1, key1)
    _save(claude_db, 2, key2)
    assert claude_agents._provider(claude_db, 1)[0] == key1
    assert claude_agents._provider(claude_db, 2)[0] == key2
    assert claude_agents.agent_status(user=_user(3), db=claude_db)["configured"] is False


def test_changing_model_does_not_erase_saved_key(claude_db):
    key = "sk-ant-" + "d" * 40
    _save(claude_db, 1, key)
    _save(claude_db, 1, None, model="claude-sonnet-4-6")
    assert claude_agents._provider(claude_db, 1) == (key, "claude-sonnet-4-6")


def test_agent_queries_fluxhub_before_returning_analysis(claude_db, monkeypatch):
    import httpx

    _save(claude_db, 1, "sk-ant-" + "a" * 40)
    monkeypatch.setattr(claude_agents, "_recent", {})
    monkeypatch.setattr(claude_agents, "build_tenant_context", lambda db, user_id: {})
    calls = []

    def execute(db, user_id, name, arguments):
        assert user_id == 1
        assert name == "desempenho_periodo"
        assert arguments == {"dias": 7}
        return {"origem": "FluxHub — pedidos", "pedidos_pagos": 12}

    class MockClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, headers, json):
            calls.append(json)
            if len(calls) == 1:
                assert json["tool_choice"] == {"type": "any"}
                return httpx.Response(200, json={"content": [{
                    "type": "tool_use", "id": "tool_1", "name": "desempenho_periodo",
                    "input": {"dias": 7},
                }]})
            assert json["tool_choice"] == {"type": "auto"}
            assert '"pedidos_pagos": 12' in json["messages"][-1]["content"][0]["content"]
            return httpx.Response(200, json={"content": [{
                "type": "text", "text": "12 pedidos pagos nos últimos 7 dias.",
            }]})

    monkeypatch.setattr(claude_agents, "execute_tool", execute)
    monkeypatch.setattr(claude_agents.httpx, "AsyncClient", MockClient)
    result = asyncio.run(claude_agents.run_agent(claude_agents.AgentInput(
        agent="sales", instruction="Quantos pedidos pagos nos últimos 7 dias?",
    ), user=_user(1), db=claude_db))
    assert result["sources"] == ["FluxHub — pedidos"]
    assert result["analysis"] == "12 pedidos pagos nos últimos 7 dias."
