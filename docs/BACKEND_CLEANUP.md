# Inventário e limpeza de rotas (27/10/2026)

A aplicação executável é `app.main:app`. `main:app` permanece como
atalho compatível para serviços antigos da VPS.

## Rotas montadas e consumidores

O middleware `app.core.api_auth.protect_fastapi` protege as rotas por
padrão; cada operação também valida o usuário/empresa conforme sua função.
Não desative OAuth, callbacks, integrações administrativas ou endpoints
de suporte somente por não terem referência direta no frontend: eles
podem ter clientes externos.

As rotas abaixo estão disponíveis sob `/api/v1`:

| Arquivo | Método | Caminho | Classificação |
| --- | --- | --- | --- |
| `admin.py` | `POST` | `/api/v1/admin/users` | Admin |
| `admin.py` | `GET` | `/api/v1/admin/users` | Admin |
| `admin.py` | `PATCH` | `/api/v1/admin/users/{user_id}/status` | Admin |
| `admin.py` | `POST` | `/api/v1/admin/users/{user_id}/reset-password` | Admin |
| `admin.py` | `GET` | `/api/v1/admin/users/{user_id}/sources` | Admin |
| `admin.py` | `GET` | `/api/v1/admin/overview` | Admin |
| `admin.py` | `GET` | `/api/v1/admin/users/{user_id}/overview` | Admin |
| `analytics.py` | `PUT` | `/api/v1/analytics/product-costs/{external_id}` | App/serviço |
| `analytics.py` | `POST` | `/api/v1/analytics/product-costs/import` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/product-costs` | App/serviço |
| `analytics.py` | `POST` | `/api/v1/analytics/ads-daily/import` | App/serviço |
| `analytics.py` | `POST` | `/api/v1/analytics/sync` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/ads-performance` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/order-status` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/cancellations` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/financial-monitoring` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/ads-profitability` | App/serviço |
| `analytics.py` | `GET` | `/api/v1/analytics/timeline` | App/serviço |
| `atendimento.py` | `GET` | `/api/v1/atendimento/contas` | Legado; manter por compatibilidade |
| `atendimento.py` | `GET` | `/api/v1/atendimento/perguntas` | Legado; manter por compatibilidade |
| `atendimento.py` | `POST` | `/api/v1/atendimento/perguntas/{question_id}/responder` | Legado; manter por compatibilidade |
| `atendimento.py` | `GET` | `/api/v1/atendimento/pedidos` | Legado; manter por compatibilidade |
| `atendimento.py` | `GET` | `/api/v1/atendimento/mensagens/{pack_id}` | Legado; manter por compatibilidade |
| `atendimento.py` | `POST` | `/api/v1/atendimento/mensagens/{pack_id}/responder` | Legado; manter por compatibilidade |
| `atendimento.py` | `POST` | `/api/v1/atendimento/sugerir` | Legado; manter por compatibilidade |
| `auth.py` | `POST` | `/api/v1/auth/login` | Autenticação |
| `auth.py` | `GET` | `/api/v1/auth/me` | Autenticação |
| `auth.py` | `POST` | `/api/v1/auth/change-password` | Autenticação |
| `connectors.py` | `GET` | `/api/v1/connectors/catalog` | Integração/OAuth |
| `connectors.py` | `GET` | `/api/v1/connectors` | Integração/OAuth |
| `connectors.py` | `PATCH` | `/api/v1/connectors/{provider}/{account_key}` | Integração/OAuth |
| `dashboard.py` | `GET` | `/api/v1/dashboard` | App/serviço |
| `health.py` | `GET` | `/api/v1/health` | Health |
| `indicators.py` | `GET` | `/api/v1/indicators/` | App/serviço |
| `mercado_livre.py` | `POST` | `/api/v1/connectors/mercado-livre/authorize` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/authorize` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/callback` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/diagnostics` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/identity` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/profile` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/items` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/items/live` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/items/{item_id}` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/orders` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/visits` | Integração/OAuth |
| `mercado_livre.py` | `GET` | `/api/v1/connectors/mercado-livre/items/{item_id}/visits` | Integração/OAuth |
| `mercado_livre.py` | `DELETE` | `/api/v1/connectors/mercado-livre` | Integração/OAuth |
| `mercado_pago.py` | `GET` | `/api/v1/connectors/mercado-pago/authorize` | Integração/OAuth |
| `mercado_pago.py` | `GET` | `/api/v1/connectors/mercado-pago/callback` | Integração/OAuth |
| `mercado_pago.py` | `GET` | `/api/v1/connectors/mercado-pago` | Integração/OAuth |
| `mercado_pago.py` | `GET` | `/api/v1/connectors/mercado-pago/payments` | Integração/OAuth |
| `mercado_pago.py` | `DELETE` | `/api/v1/connectors/mercado-pago` | Integração/OAuth |
| `orders.py` | `GET` | `/api/v1/orders/` | App/serviço |
| `orders.py` | `GET` | `/api/v1/orders/paged` | App/serviço |
| `orders.py` | `GET` | `/api/v1/orders/buyers` | App/serviço |
| `orders.py` | `GET` | `/api/v1/orders/buyers/{buyer_id}` | App/serviço |
| `orders.py` | `GET` | `/api/v1/orders/{order_id}` | App/serviço |
| `orders.py` | `POST` | `/api/v1/orders/{order_id}/refresh-items` | App/serviço |
| `orders.py` | `GET` | `/api/v1/orders/{order_id}/buyer` | App/serviço |
| `pricing.py` | `GET` | `/api/v1/pricing/{item_id}` | App/serviço |
| `pricing.py` | `PATCH` | `/api/v1/pricing/{item_id}/cost` | App/serviço |
| `product_performance.py` | `GET` | `/api/v1/analytics/items/{item_id}/performance` | App/serviço |
| `products.py` | `POST` | `/api/v1/products/import/excel` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/photos` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/count` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/top-discount` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/top-stock` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/catalog-health` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/categories` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/brands` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/out-of-stock` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/low-stock` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/low-rating` | App/serviço |
| `products.py` | `GET` | `/api/v1/products/top-rated` | App/serviço |
| `sales.py` | `GET` | `/api/v1/sales/` | App/serviço |
| `stock.py` | `GET` | `/api/v1/stock/` | App/serviço |
| `support.py` | `GET` | `/api/v1/support/threads` | App/serviço |
| `support.py` | `GET` | `/api/v1/support/threads/{reference}` | App/serviço |
| `support.py` | `POST` | `/api/v1/support/reply` | App/serviço |
| `support.py` | `POST` | `/api/v1/support/suggest` | App/serviço |
| `sync_jobs.py` | `POST` | `/api/v1/analytics/sync-jobs` | App/serviço |
| `sync_jobs.py` | `GET` | `/api/v1/analytics/sync-jobs/{account_id}` | App/serviço |
| `sync_jobs.py` | `POST` | `/api/v1/analytics/sync-jobs/{account_id}/pause` | App/serviço |
| `sync_jobs.py` | `POST` | `/api/v1/analytics/sync-jobs/{account_id}/orders` | App/serviço |

## Ações desta limpeza

- A implementação mantida reside em `app/`; diretórios de código
  duplicado na raiz (`routers/`, `services/`, `connectors/`,
  `database/`, `schemas/`, `config/`, `core/`, `workers/`)
  não eram importados pelos serviços nem pelos testes.
- Removidos dumps locais de estrutura/fonte, caches compilados e arquivos
  Node/Pnpm acidentalmente versionados no repositório Python. O arquivo
  Excel de importação utilizado pelo script canônico continua em
  `app/connectors/excel/data/`.
- Totais do catálogo, dashboard e estoque agora vêm de uma agregação SQL
  com o mesmo deduplicador por `external_id` e filtro de fontes habilitadas.
- O parser pandas passa a ser carregado quando uma planilha é importada,
  evitando seu custo de inicialização em cada worker da API.
- O frontend chama `/api/v1/support`. Os antigos sete endpoints
  `/api/v1/atendimento` não aparecem no frontend atual, mas permanecem
  marcados como **deprecated** até confirmação de que não existem consumidores
  externos. O conector Mercado Pago e os callbacks OAuth também ficam preservados.
- Nenhuma migração de PostgreSQL, remoção de contas ou reset de tokens foi feita.

## Validação

`python -m compileall -q app scripts` e suíte do PostgreSQL no GitHub
Actions. Após deploy, verificar health, login, listagem de produtos,
estoque, sync-jobs, pedidos e callback Mercado Livre. Não use
`DROP`, `TRUNCATE` ou migração destrutiva para esta atualização.
