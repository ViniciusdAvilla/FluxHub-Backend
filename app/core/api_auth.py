"""Service-to-service authentication; human JWT remains in Authorization."""
from hmac import compare_digest
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from app.config.settings import settings


def protect_fastapi(app: FastAPI) -> None:
    @app.middleware("http")
    async def require_backend_token(request: Request, call_next):
        path = request.scope.get("path", request.url.path)
        root = request.scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root):]
        prefix = settings.api_prefix.rstrip("/")
        public = {
            f"{prefix}/health",
            f"{prefix}/connectors/mercado-livre/callback",
            f"{prefix}/connectors/mercado-pago/callback",
        }
        # OAuth callbacks are secure through short-lived one-time server-side state.
        if request.method == "GET" and path in public:
            return await call_next(request)

        secret = settings.backend_token
        if not secret or len(secret) < 32 or secret != secret.strip():
            return JSONResponse({"detail": "Autenticação do backend não configurada."},
                                status_code=503, headers={"Cache-Control": "no-store"})

        service_header = request.headers.get("x-backend-token", "")
        authorization = request.headers.get("authorization", "")
        # Compatibility with the already-deployed Next.js server.
        # For end-user routes, use X-Backend-Token AND Authorization: Bearer <JWT>.
        if not service_header and authorization.startswith("Bearer "):
            service_header = authorization.removeprefix("Bearer ")

        valid = (0 < len(service_header) <= 4096 and
                 compare_digest(service_header.encode(), secret.encode()))
        if not valid:
            return JSONResponse({"detail": "Não autorizado."}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer",
                                         "Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
