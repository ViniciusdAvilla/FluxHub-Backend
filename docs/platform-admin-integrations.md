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
