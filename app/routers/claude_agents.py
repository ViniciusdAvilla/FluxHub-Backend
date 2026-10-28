"""Read-only Claude assistants over tenant-owned business aggregates.

Never sends buyer PII, order identifiers, OAuth tokens or raw seller messages.
No write actions are exposed to the model.
"""
import asyncio
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
from app.database.models import (
    UserModel, ProductModel, MercadoLivreOrderSnapshotModel as Order,
)
from app.database.session import get_db

router = APIRouter(prefix="/agents/claude", tags=["Claude Agents"])
AGENTS = {
    "sales": "Analista de vendas: identifique tendências e sugestões comerciais.",
    "pricing": "Estrategista de preço: explique margem, comissões e riscos; não invente custos.",
    "inventory": "Analista de estoque: sugira ações de reposição sem prometer níveis ideais.",
    "strategy": "Estrategista ABC: sugira indicadores e experimentos sem alterar anúncios.",
}
_recent: dict[int, float] = {}
_lock = asyncio.Lock()


class AgentInput(BaseModel):
    agent: Literal["sales", "pricing", "inventory", "strategy"]
    instruction: str = Field(default="", max_length=1200)


@router.get("/status")
def agent_status(user: UserModel = Depends(get_current_user)):
    return {
        "configured": bool(settings.anthropic_api_key and
                           settings.anthropic_api_key.get_secret_value()),
        "model": settings.claude_model,
        "modes": AGENTS,
        "read_only": True,
        "personal_data_shared": False,
    }


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
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not key:
        raise HTTPException(503, "Claude ainda não foi configurado pelo administrador.")
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
    message = (
        "Dados consolidados, sem informações pessoais: " + str(context) + "\\n"
        "Solicitação do usuário: " + (body.instruction.strip() or
        "Faça uma análise objetiva com 5 recomendações e ressalvas sobre dados ausentes.")
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.claude_model, "max_tokens": 1200,
                    "system": (
                        "Você é um agente analítico do FluxHub, em português brasileiro. "
                        + AGENTS[body.agent] +
                        " Nunca afirme que faturamento bruto é lucro. "
                        "Dados ausentes devem ser explicitamente identificados. "
                        "Você só pode recomendar; não pode executar operações, acessar "
                        "dados pessoais ou solicitar credenciais."),
                    "messages": [{"role": "user", "content": message}],
                },
            )
            if response.status_code == 429:
                raise HTTPException(429, "Limite da API Claude atingido; tente mais tarde.")
            if not response.is_success:
                raise HTTPException(502, "Falha ao consultar Claude. Verifique a configuração.")
            payload = response.json()
            content = payload.get("content") if isinstance(payload, dict) else []
            text = "\n".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()[:12000]
            if not text:
                raise HTTPException(502, "A API Claude retornou uma resposta vazia.")
            return {"agent": body.agent, "model": settings.claude_model,
                    "analysis": text, "read_only": True, "context": context}
    except httpx.TimeoutException:
        raise HTTPException(504, "Claude demorou para responder. Tente novamente.")
    except httpx.RequestError:
        raise HTTPException(502, "Não foi possível conectar à API Claude.")
