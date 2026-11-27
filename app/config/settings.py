from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "FluxHub"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    database_url: str = ""
    # Disable in API workers when running a dedicated standalone sync service.
    sync_worker_enabled: bool = True
    jwt_secret_key: SecretStr | None = None
    mercado_livre_client_id: SecretStr | None = None
    mercado_livre_client_secret: SecretStr | None = None
    # Ativar somente se o aplicativo Mercado Livre usa PKCE.
    mercado_livre_pkce_enabled: bool = False
    mercado_livre_redirect_uri: str = (
        "https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback"
    )
    # Google Shopping credentials only in the private FastAPI environment.
    serpapi_key: SecretStr | None = None
    # Optional: manual support works without this key.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: SecretStr | None = None
    claude_model: str = "claude-sonnet-5"
    mercado_pago_client_id: SecretStr | None = None
    mercado_pago_client_secret: SecretStr | None = None
    mercado_pago_redirect_uri: str = "https://myfluxhub.tech/backend-api/api/v1/connectors/mercado-pago/callback"
    frontend_url: str = "http://localhost:3000"
    token_encryption_key: SecretStr | None = None
    access_token_expire_minutes: int = 60

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @model_validator(mode="after")
    def validate_production_settings(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("FluxHub requires PostgreSQL DATABASE_URL.")
        if self.environment.lower() == "production":
            required_secrets = {
                "JWT_SECRET_KEY": self.jwt_secret_key,
                "TOKEN_ENCRYPTION_KEY": self.token_encryption_key,
            }
            missing = [
                name
                for name, value in required_secrets.items()
                if value is None or not value.get_secret_value()
            ]
            if missing:
                raise ValueError(
                    "Missing required production settings: " + ", ".join(missing)
                )
            if not self.mercado_livre_redirect_uri.startswith("https://"):
                raise ValueError("The Mercado Livre redirect URI must use HTTPS.")
            if not self.frontend_url.startswith("https://"):
                raise ValueError("The frontend URL must use HTTPS in production.")
        return self

    # Chave de serviço compartilhada somente pelo backend e Next.js.
    backend_token: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
