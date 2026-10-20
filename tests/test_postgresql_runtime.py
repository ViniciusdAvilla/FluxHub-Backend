from app.config.settings import Settings
from pydantic import ValidationError
import pytest


def test_postgresql_is_only_supported_runtime():
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(_env_file=None, environment="test", database_url="sqlite:///legacy.db")


def test_postgresql_dsn_accepted():
    settings = Settings(_env_file=None, environment="test",
                        database_url="postgresql://fluxhub:secret@127.0.0.1:5432/fluxhub")
    assert settings.database_url.startswith("postgresql+psycopg://")
