"""Autenticação servidor-a-servidor para todos os endpoints privados.

O painel Next.js manda Authorization: Bearer <BACKEND_TOKEN> somente no servidor.
O único endpoint público é GET /api/v1/health para monitoramento de disponibilidade.
"""

from hmac import compare_digest

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from app.config.settings import settings


def protect_fastapi(app: FastAPI) -> None:
    @app.middleware("http")
    async def require_backend_token(request: Request, call_next):
        # O health check não expõe dados, credenciais nem o contrato da API.
        if request.method == "GET" and request.url.path == f"{settings.api_prefix}/health":
            return await call_next(request)

        # Sem segredo forte, falhe de forma fechada: nunca exponha o banco por engano.
        secret = settings.backend_token
        if not secret or len(secret) < 32 or secret.strip() != secret:
            return JSONResponse(
                {"detail": "Autenticação do backend não configurada."},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 4096:
            return JSONResponse(
                {"detail": "Não autorizado."},
                status_code=401,
                headers={
                    "WWW-Authenticate": "Bearer",
                    "Cache-Control": "no-store",
                },
            )

        supplied = authorization.removeprefix("Bearer ")
        if not compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
            return JSONResponse(
                {"detail": "Não autorizado."},
                status_code=401,
                headers={
                    "WWW-Authenticate": "Bearer",
                    "Cache-Control": "no-store",
                },
            )

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
