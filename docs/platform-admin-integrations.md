# Plataforma FluxHub: separação de autenticação e clientes

## Headers
- Todas as rotas privadas precisam de `X-Backend-Token: <BACKEND_TOKEN>` (somente servidor/conector).
- Rotas de usuário exigem também `Authorization: Bearer <JWT_FLUXHUB>` emitido por `POST /api/v1/auth/login`.
- Compatibilidade temporária: `Authorization: Bearer <BACKEND_TOKEN>` ainda abre documentação e rotas somente de serviço; não autentica usuário.
- GET /api/v1/health é pública. Callbacks OAuth usam state opaco aleatório de uso único, validado no PostgreSQL.

## Admin e tenants
1. Faça backup do PostgreSQL, configure ambiente, `python -m scripts.migrate_platform`.
2. Crie o admin somente via SSH `python -m scripts.create_admin --email admin@exemplo.com` (senha no prompt).
3. Faça login via `POST /api/v1/auth/login` + `X-Backend-Token`.
4. Com JWT do admin, `POST /api/v1/admin/users`, `GET /api/v1/admin/users`,
   `PATCH /api/v1/admin/users/{id}/status`, `POST /api/v1/admin/users/{id}/reset-password`.
5. Cada usuário é o tenant. Produtos, snapshots e conexões possuem user_id/company_id e sempre devem ser filtrados por identidade JWT; não permita `user_id` selecionado pelo browser.
6. Isso é separação lógica em um PostgreSQL compartilhado, não banco físico por tenant.
7. Ajuste Next.js para enviar BACKEND_TOKEN exclusivamente em `X-Backend-Token` e JWT em `Authorization`; nunca exponha o segredo de serviço na web.

## Publicação
- Antes de trocar autenticação em produção, faça backup e confira quais serviços carregam `/etc/fluxhub-private/connection.env`.
- Não altere o frontend existente sem migração coordenada; o fallback do token antigo fica apenas como compatibilidade temporária.
- Atualize `MERCADO_LIVRE_REDIRECT_URI` e `MERCADO_PAGO_REDIRECT_URI` no painel de desenvolvedor para as URLs HTTPS públicas com `/backend-api/`.
- Mercado Pago requer app própria, client id e client secret; não reutilize Mercado Livre.
- Segurança: nunca registre access/refresh tokens ou códigos OAuth em logs, nem inclua credenciais em commits.
- Verifique testes antes de aplicar; o script additive não substitui a revisão formal do esquema Alembic.

## Rotas concretas
- POST /api/v1/auth/login, GET /api/v1/auth/me
- POST /api/v1/admin/users, GET /api/v1/admin/users, PATCH /api/v1/admin/users/{id}/status,
  POST /api/v1/admin/users/{id}/reset-password, GET /api/v1/admin/users/{id}/sources
- GET /api/v1/connectors, PATCH /api/v1/connectors/{provider}/{account_key}
- GET /api/v1/connectors/mercado-livre/authorize, callback, (status), profile, items,
  items/{item_id}, orders; DELETE /api/v1/connectors/mercado-livre?account_id=...
- GET /api/v1/connectors/mercado-pago/authorize, callback, (status), payments;
  DELETE /api/v1/connectors/mercado-pago?account_id=...
- POST /api/v1/analytics/sync?account_id=...&limit=50&offset=0
- GET /api/v1/analytics/ads-performance, cancellations, financial-monitoring, ads-profitability
- GET /api/v1/analytics/product-costs, PUT /api/v1/analytics/product-costs/{external_id},
  POST /api/v1/analytics/product-costs/import, POST /api/v1/analytics/ads-daily/import

## Atenção: limites atuais
- A integração Mercado Livre disponibiliza operações selecionadas de leitura, conexão e
  sincronização paginada de anúncios/pedidos. NÃO representa todas as centenas de rotas oficiais.
- A sincronização coleta no máximo 50 anúncios e 50 pedidos por chamada; percorra
  `next_offset`/offset explicitamente para páginas seguintes. Não é um worker agendado.
- `visits_30d` é ausente até que uma fonte real de visitas seja implementada.
- Analytics Ads usa relatórios importados e custo inserido por cliente; não há importação
  automática da API Product Ads, nem suporte a criação de pagamentos/payouts.
- OAuth Mercado Livre e Mercado Pago são fluxos separados; callback público consome um
  state de uso único após validação. Nunca inclua tokens em respostas.
- Um banco PostgreSQL compartilhado por isolamento lógico de tenant, não DB física por empresa.
- TESTAR em staging com PostgreSQL real, OAuth sandbox e migrações antes de produção.
- Testes legados do repositório possuem casos obsoletos e interativos. Rode somente a suite
  de plataforma em CI até modernizar os testes antigos.

## Exemplo de login (Postman)
- POST https://myfluxhub.tech/backend-api/api/v1/auth/login
- Header `X-Backend-Token` = token de serviço (apenas servidor/Admin API).
- JSON `{"email":"cliente@exemplo.com","password":"SENHA_DEFINIDA_PELO_ADMIN"}`.
- A resposta contém `access_token` de usuário.
- Chamadas privadas de usuário enviam AMBOS: `X-Backend-Token` e
  `Authorization: Bearer <access_token>`.

- Mercado Livre /items/bulk e status_code substituem o endpoint /items?ids; a API
  oficial alerta sobre descontinuação de multi-item em 25/10/2026.
- Leitura de visitas GET /connectors/mercado-livre/visits?days=30 e
  /items/{item_id}/visits?days=30 consulta API real sob tenant autenticado.
