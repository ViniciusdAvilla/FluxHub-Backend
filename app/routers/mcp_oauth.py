"""OAuth 2.1 authorization-code + PKCE for external Claude MCP clients.

Server-to-server secret is enforced by the existing FastAPI gateway; the
end-user grants consent with their FluxHub login JWT. MCP tokens are separate,
scoped, revocable and cannot be used for other FluxHub API endpoints.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.auth import get_current_user
from app.database.mcp_models import McpAuthorizationRequest, McpClient, McpGrant
from app.database.models import UserModel
from app.database.session import get_db

router = APIRouter(prefix="/mcp/oauth", tags=["MCP OAuth"])
SCOPE = "fluxhub:read"
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def public_base() -> str:
    return settings.frontend_url.rstrip("/")


def resource_url() -> str:
    return public_base() + "/mcp"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _sign(body: dict) -> str:
    if not settings.jwt_secret_key:
        raise HTTPException(503, "Assinatura do MCP indisponível.")
    secret = settings.jwt_secret_key.get_secret_value().encode("utf-8")
    header = _b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    text = header + "." + payload
    sig = _b64(hmac.new(secret, text.encode(), hashlib.sha256).digest())
    return text + "." + sig


def _verify(token: str) -> dict:
    if not settings.jwt_secret_key:
        raise ValueError("MCP indisponível")
    parts = token.split(".")
    if len(parts) != 3 or len(token) > 4096:
        raise ValueError("Token inválido")
    text = parts[0] + "." + parts[1]
    sig = _b64(hmac.new(settings.jwt_secret_key.get_secret_value().encode(),
                        text.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, parts[2]):
        raise ValueError("Assinatura inválida")
    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    if header.get("alg") != "HS256" or claims.get("aud") != resource_url() or (
        claims.get("iss") != public_base()) or claims.get("type") != "mcp" or (
        not isinstance(claims.get("exp"), int) or claims["exp"] <= time.time()
    ):
        raise ValueError("Token MCP inválido ou expirado")
    return claims


def authorized_user(db: Session, token: str) -> UserModel:
    try:
        claims = _verify(token)
        grant = db.get(McpGrant, claims["gid"])
        user = db.get(UserModel, int(claims["sub"]))
        if (not grant or not user or grant.revoked_at or
            _utc(grant.expires_at) <= now_utc() or grant.user_id != user.id or
            not user.is_active or user.token_version != claims.get("ver") or
            grant.token_version != user.token_version):
            raise ValueError("Autorização revogada")
        return user
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise HTTPException(401, "Conecte novamente seu FluxHub ao Claude.") from error


def _token_response(db: Session, grant: McpGrant, user: UserModel, refresh: str) -> dict:
    access = _sign({
        "iss": public_base(), "aud": resource_url(), "type": "mcp",
        "sub": str(user.id), "ver": user.token_version, "gid": grant.id,
        "iat": int(time.time()), "exp": int(time.time()) + 3600,
    })
    return {
        "access_token": access, "refresh_token": refresh, "token_type": "Bearer",
        "expires_in": 3600, "scope": SCOPE,
    }


class ClientInput(BaseModel):
    client_name: str = Field(default="FluxHub Connector", min_length=1, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=5)
    token_endpoint_auth_method: str = "none"


class ApprovalInput(BaseModel):
    request_id: str = Field(min_length=20, max_length=100)


def _redirect_valid(url: str) -> bool:
    if len(url) > 2048 or any(ord(c) < 32 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        return bool(
            parsed.scheme == "https" and parsed.hostname
            and not parsed.username and not parsed.password
            and not parsed.fragment and parsed.port in (None, 443)
        ) or bool(parsed.scheme == "http" and parsed.hostname in (
            "127.0.0.1", "localhost", "::1"
        ) and not parsed.username and not parsed.password and not parsed.fragment)
    except ValueError:
        return False


@router.post("/register", status_code=201)
def register_client(body: ClientInput, db: Session = Depends(get_db)):
    if body.token_endpoint_auth_method != "none" or not all(
        _redirect_valid(uri) for uri in body.redirect_uris
    ) or len(set(body.redirect_uris)) != len(body.redirect_uris):
        raise HTTPException(422, "URLs de retorno ou tipo de cliente inválidos.")
    # Bound anonymous dynamic registrations; no unbounded table growth on a public endpoint.
    if (db.scalar(select(func.count(McpClient.id))) or 0) >= 2000:
        raise HTTPException(429, "Limite temporário de clientes MCP atingido.")
    client = McpClient(
        id="fc_" + secrets.token_urlsafe(24),
        name=body.client_name,
        redirect_uris=body.redirect_uris,
    )
    db.add(client)
    db.commit()
    return {
        "client_id": client.id, "client_name": client.name,
        "redirect_uris": client.redirect_uris, "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


@router.get("/authorize")
def authorize(
    client_id: str = Query(min_length=10, max_length=80),
    redirect_uri: str = Query(min_length=8, max_length=2048),
    response_type: str = Query(default="code"),
    code_challenge: str = Query(min_length=43, max_length=128),
    code_challenge_method: str = Query(default="S256"),
    state: str = Query(default="", max_length=512),
    scope: str = Query(default=SCOPE, max_length=128),
    resource: str | None = Query(default=None, max_length=2048),
    db: Session = Depends(get_db),
):
    client = db.get(McpClient, client_id)
    if not client or redirect_uri not in client.redirect_uris:
        raise HTTPException(400, "Cliente ou URL de retorno não reconhecidos.")
    if (response_type != "code" or code_challenge_method != "S256"
        or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", code_challenge)
        or scope not in ("", SCOPE) or (resource is not None and resource != resource_url())):
        raise HTTPException(400, "Solicitação OAuth incompatível; use PKCE S256.")
    pending = McpAuthorizationRequest(
        id=secrets.token_urlsafe(32), client_id=client_id,
        redirect_uri=redirect_uri, challenge=code_challenge,
        state=state, scope=SCOPE, expires_at=now_utc() + timedelta(minutes=10),
    )
    db.add(pending)
    db.commit()
    return {"consent_url": public_base() + "/mcp/autorizar?" + urlencode({"request_id": pending.id})}


@router.get("/request/{request_id}")
def describe_request(
    request_id: str, user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pending = db.get(McpAuthorizationRequest, request_id)
    if not pending or pending.code_hash or _utc(pending.expires_at) <= now_utc():
        raise HTTPException(410, "Solicitação de conexão expirada.")
    client = db.get(McpClient, pending.client_id)
    return {
        "client_name": client.name if client else "Aplicativo externo",
        "scope": pending.scope,
        "expires_at": pending.expires_at.isoformat(),
    }


@router.post("/approve")
def approve(
    body: ApprovalInput, user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pending = db.scalar(select(McpAuthorizationRequest).where(
        McpAuthorizationRequest.id == body.request_id,
    ).with_for_update())
    if not pending or pending.code_hash or _utc(pending.expires_at) <= now_utc():
        raise HTTPException(410, "Autorização expirada; inicie novamente no Claude.")
    code = secrets.token_urlsafe(40)
    pending.user_id = user.id
    pending.code_hash = digest(code)
    db.commit()
    params = {"code": code}
    if pending.state:
        params["state"] = pending.state
    separator = "&" if "?" in pending.redirect_uri else "?"
    return {"redirect_to": pending.redirect_uri + separator + urlencode(params)}


@router.post("/token")
def exchange(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: str = Form(default=""),
    redirect_uri: str = Form(default=""),
    code_verifier: str = Form(default=""),
    refresh_token: str = Form(default=""),
    db: Session = Depends(get_db),
):
    client = db.get(McpClient, client_id)
    if not client:
        raise HTTPException(400, "invalid_client")
    if grant_type == "authorization_code":
        pending = db.scalar(select(McpAuthorizationRequest).where(
            McpAuthorizationRequest.code_hash == digest(code),
            McpAuthorizationRequest.client_id == client_id,
        ).with_for_update()) if code else None
        if not pending or pending.consumed_at or _utc(pending.expires_at) <= now_utc() or (
            pending.redirect_uri != redirect_uri or not pending.user_id
        ):
            raise HTTPException(400, "invalid_grant")
        verifier_bytes = hashlib.sha256(code_verifier.encode()).digest()
        if not (43 <= len(code_verifier) <= 128 and
                hmac.compare_digest(_b64(verifier_bytes), pending.challenge)):
            raise HTTPException(400, "invalid_grant")
        user = db.get(UserModel, pending.user_id)
        if not user or not user.is_active:
            raise HTTPException(400, "invalid_grant")
        pending.consumed_at = now_utc()
        refresh = secrets.token_urlsafe(48)
        grant = McpGrant(
            id=secrets.token_urlsafe(24), client_id=client_id,
            user_id=user.id, refresh_hash=digest(refresh),
            token_version=user.token_version, expires_at=now_utc() + timedelta(days=90),
        )
        db.add(grant)
        response = _token_response(db, grant, user, refresh)
        db.commit()
        return response
    if grant_type == "refresh_token":
        grant = db.scalar(select(McpGrant).where(
            McpGrant.refresh_hash == digest(refresh_token),
            McpGrant.client_id == client_id,
        ).with_for_update()) if refresh_token else None
        if not grant or grant.revoked_at or _utc(grant.expires_at) <= now_utc():
            raise HTTPException(400, "invalid_grant")
        user = db.get(UserModel, grant.user_id)
        if not user or not user.is_active or grant.token_version != user.token_version:
            raise HTTPException(400, "invalid_grant")
        fresh = secrets.token_urlsafe(48)
        grant.refresh_hash = digest(fresh)
        result = _token_response(db, grant, user, fresh)
        db.commit()
        return result
    raise HTTPException(400, "unsupported_grant_type")


@router.get("/status")
def connection_status(user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    active = db.scalar(select(func.count(McpGrant.id)).where(
        McpGrant.user_id == user.id, McpGrant.revoked_at.is_(None),
        McpGrant.expires_at > now_utc(),
    )) or 0
    return {"connected_clients": int(active), "mcp_url": resource_url()}


@router.post("/revoke-all")
def revoke_all(user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(update(McpGrant).where(
        McpGrant.user_id == user.id, McpGrant.revoked_at.is_(None),
    ).values(revoked_at=now_utc()))
    db.commit()
    return {"ok": True}
