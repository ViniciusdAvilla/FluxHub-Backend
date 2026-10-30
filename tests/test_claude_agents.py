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
