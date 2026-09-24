from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    app_name: str = "Middleware E-commerce"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///middleware_ecommerce.db"


settings = Settings()