"""Mercado Pago OAuth (application credentials stay server-side)."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from app.config.settings import settings
from app.core.security import decrypt_token, encrypt_token

API_URL = "https://api.mercadopago.com"
AUTH_URL = "https://auth.mercadopago.com/authorization"


class MercadoPagoError(Exception):
    pass


def credentials():
    app_id = settings.mercado_pago_client_id
    secret = settings.mercado_pago_client_secret
    if not app_id or not app_id.get_secret_value() or not secret or not secret.get_secret_value():
        raise RuntimeError("Mercado Pago OAuth não configurado.")
    return app_id.get_secret_value(), secret.get_secret_value()


def authorization_url(state: str) -> str:
    app_id, _ = credentials()
    return AUTH_URL + "?" + urlencode({
        "response_type": "code", "client_id": app_id,
        "redirect_uri": settings.mercado_pago_redirect_uri,
        "platform_id": "mp", "state": state})


async def oauth_token(body: dict) -> dict:
    app_id, secret = credentials()
    payload = {"client_id": app_id, "client_secret": secret, **body}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(f"{API_URL}/oauth/token", json=payload,
                                         headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise MercadoPagoError("Mercado Pago OAuth falhou.") from error
    if not all(data.get(k) for k in ("access_token", "refresh_token", "user_id", "expires_in")):
        raise MercadoPagoError("Resposta inválida do Mercado Pago.")
    return data


def expires_at(data: dict) -> datetime:
    try:
        seconds = int(data["expires_in"])
        if seconds <= 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise MercadoPagoError("Expiração OAuth inválida.")
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def active_token(connection, db):
    expires = connection.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires > datetime.now(timezone.utc) + timedelta(minutes=5):
        return decrypt_token(connection.access_token_encrypted)
    refresh = decrypt_token(connection.refresh_token_encrypted)
    result = await oauth_token({"grant_type": "refresh_token", "refresh_token": refresh})
    connection.access_token_encrypted = encrypt_token(str(result["access_token"]))
    connection.refresh_token_encrypted = encrypt_token(str(result["refresh_token"]))
    connection.expires_at = expires_at(result)
    db.commit()
    return decrypt_token(connection.access_token_encrypted)
