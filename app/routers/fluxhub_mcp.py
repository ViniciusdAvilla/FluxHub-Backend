"""Stateless Streamable HTTP MCP endpoint for read-only FluxHub analytics.

Only MCP JSON-RPC methods are accepted. Auth is an independently minted,
revocable MCP OAuth access token, never a FluxHub login token.
"""
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.routers.mcp_oauth import authorized_user, resource_url, public_base
from app.services.claude_tools import TOOL_DEFINITIONS, execute_tool

router = APIRouter(prefix="/mcp", tags=["FluxHub MCP"])
logger = logging.getLogger(__name__)


def _error(identifier, code: int, message: str) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": "2.0", "id": identifier,
        "error": {"code": code, "message": message},
    }, headers={"Cache-Control": "no-store"})


@router.post("")
@router.post("/")
async def mcp_handler(request: Request, db: Session = Depends(get_db)):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return JSONResponse(
            {"error": "OAuth MCP requerido."}, status_code=401,
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{public_base()}/.well-known/oauth-protected-resource"',
                "Cache-Control": "no-store",
            },
        )
    try:
        user = authorized_user(db, authorization[7:])
    except Exception as error:
        if getattr(error, "status_code", None) != 401:
            raise
        return JSONResponse({"error": "Token MCP inválido ou revogado."}, status_code=401,
                            headers={
                                "WWW-Authenticate": f'Bearer resource_metadata="{public_base()}/.well-known/oauth-protected-resource"',
                                "Cache-Control": "no-store",
                            })
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return JSONResponse({"error": "application/json obrigatório"}, status_code=415)
    raw = await request.body()
    if len(raw) > 48_000:
        return JSONResponse({"error": "Requisição muito grande"}, status_code=413)
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _error(None, -32700, "JSON inválido")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Solicitação MCP inválida")
    method = message.get("method")
    identifier = message.get("id")
    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fluxhub", "version": "1.0.0"},
            "instructions": "Ferramentas somente de leitura com dados do seu vendedor no FluxHub. Dados podem ser parciais.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOL_DEFINITIONS}
    elif method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(identifier, -32602, "Ferramenta inválida")
        try:
            output = execute_tool(db, user.id, params["name"], params.get("arguments"))
            result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
                      "structuredContent": output, "isError": False}
        except ValueError as error:
            result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
        except Exception:
            logger.exception("fluxhub_mcp_tool_failed tool=%s", params["name"])
            result = {"content": [{"type": "text", "text": "Consulta indisponível. Tente novamente."}],
                      "isError": True}
    else:
        return _error(identifier, -32601, "Método MCP não disponível")
    return JSONResponse({"jsonrpc": "2.0", "id": identifier, "result": result},
                        headers={"Cache-Control": "no-store"})


@router.get("")
@router.get("/")
def no_stream():
    # Stateless Streamable HTTP does not maintain a resumable SSE channel.
    return Response(status_code=405, headers={"Allow": "POST"})
