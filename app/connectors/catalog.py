"""Catálogo público de capacidades do SaaS; nunca representa autorização do vendedor.

Adicionar novos providers aqui não os conecta automaticamente: cada provider
precisa de sua própria autorização, client, refresh, webhooks e testes.
"""

PROVIDERS = (
    {
        "id": "mercado_livre",
        "name": "Mercado Livre",
        "status": "available",
        "auth_type": "oauth2_authorization_code",
        "allows_multiple_accounts": True,
        "features": ["seller_profile", "listings", "orders", "visits", "sync"],
    },
    {
        "id": "mercado_pago",
        "name": "Mercado Pago",
        "status": "planned",
        "auth_type": "oauth2_authorization_code",
        "allows_multiple_accounts": True,
        "features": [],
    },
    {
        "id": "shopee",
        "name": "Shopee",
        "status": "planned",
        "auth_type": "partner_authorization",
        "allows_multiple_accounts": True,
        "features": [],
    },
    {
        "id": "tiktok_shop",
        "name": "TikTok Shop",
        "status": "planned",
        "auth_type": "partner_authorization",
        "allows_multiple_accounts": True,
        "features": [],
    },
)

def provider_catalog():
    return [dict(p, features=list(p["features"])) for p in PROVIDERS]
