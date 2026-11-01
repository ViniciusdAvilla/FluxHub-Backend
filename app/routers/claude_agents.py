"""Claude analytics for authenticated FluxHub tenants.

Each authenticated seller may configure an encrypted personal Anthropic API key.
Business context is read-only, aggregated and scoped to the logged-in seller.
No buyer PII, OAuth credentials or order identifiers are sent to Anthropic.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.auth import get_current_user
from app.core.security import decrypt_token, encrypt_token
from app.database.models import (
    ClaudeProviderConfigModel,
    MercadoLivreOrderSnapshotModel as Order,
    ProductModel,
    UserModel,
)
from app.database.session import get_db
from app.services.claude_tools import TOOL_DEFINITIONS, execute_tool

router = APIRouter(prefix="/agents/claude", tags=["Claude Agents"])
AGENTS = {
    "sales": "Analista de vendas: identifique tendências e sugestões comerciais.",
    "pricing": "Estrategista de preço: explique margem, comissões e riscos; não invente custos.",
    "inventory": "Analista de estoque: sugira ações de reposição sem prometer níveis ideais.",
    "strategy": "Estrategista ABC: sugira indicadores e experimentos sem alterar anúncios.",
}
MODELS = (
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
)
_recent: dict[int, float] = {}
_lock = asyncio.Lock()


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class AgentInput(BaseModel):
    agent: Literal["sales", "pricing", "inventory", "strategy"]
    instruction: str = Field(default="", max_length=1200)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=8)


class ClaudeConfigInput(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)
    model: Literal[
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
    ] = "claude-sonnet-5"


def _provider(db: Session, user_id: int) -> tuple[str, str]:
    saved = db.get(ClaudeProviderConfigModel, user_id)
    env_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if saved:
        if saved.api_key_encrypted:
            try:
                return decrypt_token(saved.api_key_encrypted), saved.model
            except (RuntimeError, ValueError) as error:
                raise HTTPException(503, "Não foi possível ler a chave do Claude no servidor.") from error
        return env_key, saved.model
    return env_key, settings.claude_model


@router.get("/status")
def agent_status(user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    key, model = _provider(db, user.id)
    own_config = db.get(ClaudeProviderConfigModel, user.id)
    return {
        "configured": bool(key),
        "connection_state": "key_saved" if key else "not_configured",
        "mcp_available": True,
        "model": model,
        "models": MODELS,
        "modes": AGENTS,
        "read_only": True,
        "personal_data_shared": False,
        "can_manage": True,
        "personal_key": bool(own_config and own_config.api_key_encrypted),
    }


def _anthropic_error(response: httpx.Response) -> HTTPException:
    code = response.status_code
    if code in (401, 403):
        return HTTPException(422, "Chave Anthropic inválida ou sem permissão.")
    if code == 402:
        return HTTPException(402, "A API Anthropic está sem créditos ou com problema de cobrança. Consulte o Console Anthropic.")
    if code == 404:
        return HTTPException(422, "Modelo Claude não encontrado ou indisponível nesta conta.")
    if code == 429:
        return HTTPException(429, "Limite de requisições Anthropic atingido. Aguarde antes de tentar novamente.")
    if code == 400:
        return HTTPException(422, "A Anthropic rejeitou a requisição. Confira o modelo e os limites de gastos do workspace.")
    if code in (500, 502, 503, 504, 529):
        return HTTPException(503, "A API Anthropic está temporariamente indisponível. Tente novamente.")
    return HTTPException(502, f"A Anthropic não respondeu como esperado (HTTP {code}).")


async def _check_anthropic(key: str, model: str) -> None:
    """A successful model lookup alone does not prove Messages API availability."""
    if not key or len(key) < 20:
        raise HTTPException(422, "Informe uma chave válida da API Anthropic.")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            model_response = await client.get(
                "https://api.anthropic.com/v1/models/" + model, headers=headers)
            if not model_response.is_success:
                raise _anthropic_error(model_response)
            message_response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={**headers, "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 16,
                      "messages": [{"role": "user", "content": "Responda OK."}]},
            )
            if not message_response.is_success:
                raise _anthropic_error(message_response)
            if not any(block.get("type") == "text" for block in
                       message_response.json().get("content", [])):
                raise HTTPException(502, "Claude não retornou texto no teste real.")
    except httpx.TimeoutException as error:
        raise HTTPException(504, "A Anthropic demorou para responder.") from error
    except httpx.RequestError as error:
        raise HTTPException(502, "Não foi possível conectar à Anthropic.") from error


@router.post("/config/test")
async def test_config(
    body: ClaudeConfigInput,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_key, _ = _provider(db, user.id)
    provided = body.api_key.strip() if body.api_key is not None else ""
    await _check_anthropic(provided or existing_key, body.model)
    return {"ok": True, "model": body.model}


@router.post("/config")
async def save_config(
    body: ClaudeConfigInput,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provided = body.api_key.strip() if body.api_key is not None else ""
    if provided and len(provided) < 20:
        raise HTTPException(422, "A chave deve conter pelo menos 20 caracteres.")
    current = db.get(ClaudeProviderConfigModel, user.id)
    env_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not provided and not (current and current.api_key_encrypted) and not env_key:
        raise HTTPException(422, "Informe a chave da Anthropic para ativar.")
    await _check_anthropic(provided or _provider(db, user.id)[0], body.model)
    if current is None:
        current = ClaudeProviderConfigModel(user_id=user.id)
        db.add(current)
    if provided:
        try:
            current.api_key_encrypted = encrypt_token(provided)
        except (RuntimeError, ValueError) as error:
            raise HTTPException(503, "Criptografia não configurada no backend.") from error
    current.model = body.model
    current.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "configured": True, "model": current.model}


def build_tenant_context(db: Session, user_id: int) -> dict:
    count = db.scalar(select(func.count(ProductModel.id)).where(
        ProductModel.company_id == user_id)) or 0
    low_stock = db.scalar(select(func.count(ProductModel.id)).where(
        ProductModel.company_id == user_id, ProductModel.stock_qty == 0)) or 0
    since = datetime.now(timezone.utc) - timedelta(days=30)
    paid = db.execute(select(
        func.count(Order.id), func.sum(Order.total_amount),
    ).where(
        Order.user_id == user_id, Order.status == "paid",
        Order.is_cancelled.is_(False), Order.created_at >= since,
    )).one()
    return {
        "products_in_catalog": int(count),
        "out_of_stock_products": int(low_stock),
        "paid_orders_30_days": int(paid[0] or 0),
        "gross_paid_sales_30_days": round(float(paid[1] or 0), 2),
        "warning": "Gross paid sales are not settled funds or net profit. Data can be partial.",
    }


@router.post("/run")
async def run_agent(
    body: AgentInput,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    key, model = _provider(db, user.id)
    if not key:
        raise HTTPException(503, "Configure sua chave Claude para utilizar o assistente.")
    async with _lock:
        now = monotonic()
        if now - _recent.get(user.id, -1e9) < 30:
            raise HTTPException(429, "Aguarde 30 segundos antes de uma nova análise.")
        _recent[user.id] = now
        if len(_recent) > 2000:
            for user_id, timestamp in list(_recent.items()):
                if now - timestamp > 60:
                    del _recent[user_id]

    context = build_tenant_context(db, user.id)
    question = body.instruction.strip() or "Analise os indicadores disponíveis e sugira melhorias."
    messages = [{"role": entry.role, "content": entry.content} for entry in body.history]
    messages.append({"role": "user", "content": question})
    headers = {
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    system_prompt = (
        "Você é o assistente FluxHub em português brasileiro. " + AGENTS[body.agent] +
        " Você pode consultar SOMENTE as ferramentas de leitura disponibilizadas. "
        "Para dados e números reais, use as ferramentas; não invente vendas, margens ou custos. "
        "Se faltarem dados, informe claramente. Faturamento bruto não é lucro líquido. "
        "Você não pode executar operações no marketplace nem acessar dados pessoais de compradores."
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
            last_payload = None
            for turn in range(4):
                response = await client.post(
                    "https://api.anthropic.com/v1/messages", headers=headers,
                    json={
                        "model": model, "max_tokens": 1800,
                        "system": system_prompt, "messages": messages,
                        "tools": TOOL_DEFINITIONS,
                    },
                )
                if not response.is_success:
                    raise _anthropic_error(response)
                payload = response.json()
                last_payload = payload
                content = payload.get("content", [])
                calls = [block for block in content if
                         isinstance(block, dict) and block.get("type") == "tool_use"]
                if not calls:
                    answer = "\n".join(block.get("text", "") for block in content
                                       if isinstance(block, dict) and block.get("type") == "text").strip()
                    if not answer:
                        raise HTTPException(502, "Claude retornou uma resposta vazia.")
                    return {
                        "agent": body.agent, "model": model, "analysis": answer[:12000],
                        "read_only": True, "context": context,
                    }
                if turn == 3:
                    raise HTTPException(503, "A análise exigiu muitas consultas. Faça uma pergunta mais específica.")
                if len(calls) > 5:
                    raise HTTPException(503, "Muitas ferramentas solicitadas em uma única resposta.")
                messages.append({"role": "assistant", "content": content})
                results = []
                for call in calls[:5]:
                    try:
                        output = execute_tool(db, user.id, call.get("name", ""), call.get("input"))
                        result = json.dumps(output, ensure_ascii=False)
                        failed = False
                    except ValueError as error:
                        result = str(error)
                        failed = True
                    results.append({
                        "type": "tool_result", "tool_use_id": call["id"],
                        "content": result, "is_error": failed,
                    })
                messages.append({"role": "user", "content": results})
    except httpx.TimeoutException as error:
        raise HTTPException(504, "Claude demorou para responder. Tente novamente.") from error
    except httpx.RequestError as error:
        raise HTTPException(502, "Não foi possível conectar à API Claude.") from error
