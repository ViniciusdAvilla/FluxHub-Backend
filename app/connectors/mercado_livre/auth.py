"""Mercado Livre OAuth: PKCE opcional e erros diagnosticáveis sem expor segredos."""
import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.security import decrypt_token, encrypt_token
from app.database.models import MercadoLivreConnectionModel


AUTHORIZATION_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
API_BASE_URL = "https://api.mercadolibre.com"
logger = logging.getLogger(__name__)

# Valores publicamente definidos pelo protocolo; não registramos corpos de respostas,
# URLs com code/state nem exceções HTTP que possam conter parâmetros sensíveis.
KNOWN_PROVIDER_ERRORS = frozenset({
    "invalid_grant", "invalid_client", "invalid_request",
    "unauthorized_client", "invalid_scope", "invalid_operator_user_id",
    "access_denied", "forbidden", "server_error",
})


class MercadoLivreOAuthError(Exception):
    def __init__(
        self, message: str, *,
        phase: str = "unknown",
        http_status: int | None = None,
        provider_error: str = "unknown",
    ):
        super().__init__(message)
        self.phase = phase
        self.http_status = http_status
        self.provider_error = provider_error


def _required_setting(value, label: str) -> str:
    if value is None or not value.get_secret_value():
        raise RuntimeError(f"Mercado Livre {label} is not configured.")
    return value.get_secret_value()


def pkce_code_verifier(state: str) -> str:
    """Verificador estável durante 10 min, vinculado ao state e a segredo privado.

    Não requer nova coluna no PostgreSQL nem carrega verifier para o navegador.
    SHA-256(HMAC(chave, domínio + state)) produz um verificador de 43 chars.
    """
    secret = _required_setting(settings.jwt_secret_key, "PKCE server key")
    digest = hmac.new(
        secret.encode("utf-8"),
        b"fluxhub-mercado-livre-pkce-v1:" + state.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorization_url(state: str) -> str:
    client_id = _required_setting(settings.mercado_livre_client_id, "client ID")
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": settings.mercado_livre_redirect_uri,
        "state": state,
    }
    if settings.mercado_livre_pkce_enabled:
        verifier = pkce_code_verifier(state)
        challenge = hashlib.sha256(verifier.encode("ascii")).digest()
        query["code_challenge"] = base64.urlsafe_b64encode(challenge).decode("ascii").rstrip("=")
        query["code_challenge_method"] = "S256"
    return f"{AUTHORIZATION_URL}?{urlencode(query)}"


async def exchange_authorization_code(code: str, state: str | None = None) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.mercado_livre_redirect_uri,
    }
    if settings.mercado_livre_pkce_enabled:
        if not state:
            raise MercadoLivreOAuthError("OAuth state required for PKCE.", phase="pkce")
        payload["code_verifier"] = pkce_code_verifier(state)
    return await _request_token(payload)


async def refresh_access_token(refresh_token: str) -> dict:
    # O refresh_token é de uso único. A operação fica sob lock da linha no banco.
    return await _request_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


async def get_valid_access_token(
    connection: MercadoLivreConnectionModel, db: Session
) -> str:
    expires_at = connection.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at > datetime.now(timezone.utc) + timedelta(minutes=2):
        return decrypt_token(connection.access_token_encrypted)

    # Um mesmo refresh token não pode ser usado por duas solicitações simultâneas.
    # PostgreSQL serializa o refresh entre workers via SELECT FOR UPDATE.
    try:
        db.scalar(select(MercadoLivreConnectionModel)
                  .where(MercadoLivreConnectionModel.id == connection.id)
                  .with_for_update())
        db.refresh(connection)
        expires_at = connection.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
            tokens = await refresh_access_token(
                decrypt_token(connection.refresh_token_encrypted)
            )
            connection.access_token_encrypted = encrypt_token(str(tokens["access_token"]))
            connection.refresh_token_encrypted = encrypt_token(str(tokens["refresh_token"]))
            connection.expires_at = token_expiration(tokens)
        token = decrypt_token(connection.access_token_encrypted)
        db.commit()
        return token
    except (MercadoLivreOAuthError, RuntimeError, ValueError, KeyError):
        db.rollback()
        raise


async def fetch_profile(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.get(
                f"{API_BASE_URL}/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            profile = response.json()
        except httpx.HTTPStatusError as error:
            logger.warning("[mercado-livre-oauth] phase=profile status=%s provider_error=%s",
                           error.response.status_code, _provider_error(error.response))
            raise MercadoLivreOAuthError(
                "Mercado Livre account profile could not be retrieved.",
                phase="profile",
                http_status=error.response.status_code,
                provider_error=_provider_error(error.response),
            ) from None
        except (httpx.RequestError, ValueError):
            logger.warning("[mercado-livre-oauth] phase=profile status=unavailable provider_error=transport")
            raise MercadoLivreOAuthError(
                "Mercado Livre profile request failed.", phase="profile",
                provider_error="transport",
            ) from None

    if not isinstance(profile, dict) or not profile.get("id"):
        raise MercadoLivreOAuthError(
            "Mercado Livre returned an invalid profile.", phase="profile",
            provider_error="invalid_response",
        )
    return profile


def token_expiration(token_response: dict) -> datetime:
    try:
        expires_in = int(token_response["expires_in"])
    except (KeyError, TypeError, ValueError) as error:
        raise MercadoLivreOAuthError(
            "Mercado Livre returned an invalid token expiration.",
            phase="token_response", provider_error="invalid_response",
        ) from error
    if expires_in <= 0:
        raise MercadoLivreOAuthError(
            "Mercado Livre returned an expired access token.",
            phase="token_response", provider_error="invalid_response",
        )
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def _provider_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
    except (ValueError, TypeError):
        error = None
    return error if isinstance(error, str) and error in KNOWN_PROVIDER_ERRORS else "unknown"


async def _request_token(payload: dict) -> dict:
    client_id = _required_setting(settings.mercado_livre_client_id, "client ID")
    client_secret = _required_setting(settings.mercado_livre_client_secret, "client secret")
    phase = "token_refresh" if payload.get("grant_type") == "refresh_token" else "token_exchange"
    body = {**payload, "client_id": client_id, "client_secret": client_secret}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(TOKEN_URL, data=body)
            response.raise_for_status()
            token_response = response.json()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            provider_error = _provider_error(error.response)
            logger.warning("[mercado-livre-oauth] phase=%s status=%s provider_error=%s",
                           phase, status, provider_error)
            raise MercadoLivreOAuthError(
                "Mercado Livre rejected the OAuth token request.",
                phase=phase, http_status=status,
                provider_error=provider_error,
            ) from None
        except (httpx.RequestError, ValueError):
            logger.warning("[mercado-livre-oauth] phase=%s status=unavailable provider_error=transport",
                           phase)
            raise MercadoLivreOAuthError(
                "Mercado Livre OAuth request failed.",
                phase=phase, provider_error="transport",
            ) from None
    if not isinstance(token_response, dict) or not all(
        token_response.get(name)
        for name in ("access_token", "refresh_token", "user_id", "expires_in")
    ):
        raise MercadoLivreOAuthError(
            "Mercado Livre returned an invalid token response.",
            phase=phase, provider_error="invalid_response",
        )
    return token_response
