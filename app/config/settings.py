from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "FluxHub"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    database_url: str = "sqlite:///middleware_ecommerce.db"
<<<<<<< HEAD
    jwt_secret_key: SecretStr | None = None
    mercado_livre_client_id: SecretStr | None = None
    mercado_livre_client_secret: SecretStr | None = None
    mercado_livre_redirect_uri: str = (
        "https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback"
    )
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
        if self.environment.lower() == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL.")
            required_secrets = {
                "JWT_SECRET_KEY": self.jwt_secret_key,
                "MERCADO_LIVRE_CLIENT_ID": self.mercado_livre_client_id,
                "MERCADO_LIVRE_CLIENT_SECRET": self.mercado_livre_client_secret,
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

    model_config = {"env_file": ".env", "extra": "ignore"}
=======
    # Compartilhado APENAS com o servidor Next.js; nunca exponha ao navegador.
    backend_token: str = ""
>>>>>>> 0f0be375cc57f2cc8768c8753476903afcddb5c7


settings = Settings()