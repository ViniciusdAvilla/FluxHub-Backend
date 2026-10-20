from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.security import decrypt_token, encrypt_token
from app.database.models import MercadoLivreConnectionModel


AUTHORIZATION_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
API_BASE_URL = "https://api.mercadolibre.com"


class MercadoLivreOAuthError(Exception):
    pass


def build_authorization_url(state: str) -> str:
    client_id = _required_setting(settings.mercado_livre_client_id, "client ID")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": settings.mercado_livre_redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZATION_URL}?{query}"


async def exchange_authorization_code(code: str) -> dict:
    return await _request_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.mercado_livre_redirect_uri,
        }
    )


async def refresh_access_token(refresh_token: str) -> dict:
    return await _request_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )


async def get_valid_access_token(
    connection: MercadoLivreConnectionModel, db: Session
) -> str:
    expires_at = connection.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at > datetime.now(timezone.utc) + timedelta(minutes=2):
        return decrypt_token(connection.access_token_encrypted)

    try:
        tokens = await refresh_access_token(
            decrypt_token(connection.refresh_token_encrypted)
        )
        connection.access_token_encrypted = encrypt_token(
            str(tokens["access_token"])
        )
        connection.refresh_token_encrypted = encrypt_token(
            str(tokens["refresh_token"])
        )
        connection.expires_at = token_expiration(tokens)
        db.commit()
    except (MercadoLivreOAuthError, RuntimeError, ValueError):
        db.rollback()
        raise
    return decrypt_token(connection.access_token_encrypted)


async def fetch_profile(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.get(
                f"{API_BASE_URL}/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise MercadoLivreOAuthError(
                "Mercado Livre account profile could not be retrieved."
            ) from error
    profile = response.json()
    if not isinstance(profile, dict) or not profile.get("id"):
        raise MercadoLivreOAuthError("Mercado Livre returned an invalid profile.")
    return profile


def token_expiration(token_response: dict) -> datetime:
    try:
        expires_in = int(token_response["expires_in"])
    except (KeyError, TypeError, ValueError) as error:
        raise MercadoLivreOAuthError(
            "Mercado Livre returned an invalid token expiration."
        ) from error
    if expires_in <= 0:
        raise MercadoLivreOAuthError("Mercado Livre returned an expired access token.")
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


async def _request_token(payload: dict) -> dict:
    client_id = _required_setting(settings.mercado_livre_client_id, "client ID")
    client_secret = _required_setting(
        settings.mercado_livre_client_secret, "client secret"
    )
    body = {
        **payload,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(TOKEN_URL, data=body)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise MercadoLivreOAuthError(
                "Mercado Livre rejected the OAuth token request."
            ) from error
    token_response = response.json()
    if not isinstance(token_response, dict) or not all(
        token_response.get(name)
        for name in ("access_token", "refresh_token", "user_id", "expires_in")
    ):
        raise MercadoLivreOAuthError("Mercado Livre returned an invalid token response.")
    return token_response


def _required_setting(value, label: str) -> str:
    if value is None or not value.get_secret_value():
        raise RuntimeError(f"Mercado Livre {label} is not configured.")
    return value.get_secret_value()