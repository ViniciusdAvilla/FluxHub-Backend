# FluxHub SaaS — Mercado Livre e catálogo multi-marketplace

## Fluxo implementado (OAuth 2.0 / Authorization Code)
1. Cliente cria/entra em sua conta FluxHub. O Next.js mantém sessão
   cifrada httpOnly com JWT do FastAPI.
2. Em **Integrações**, clica **Conectar Mercado Livre**.
3. O browser faz POST de mesma origem a
   `/api/platform/connections/mercado-livre` (Next.js). O Next.js
   encaminha POST autenticado à rota interna FastAPI
   `/api/v1/connectors/mercado-livre/authorize` usando **X-Backend-Token
   + JWT do cliente**.
4. O FastAPI cria `state` randômico, com hash salvo no PostgreSQL,
   expiração de 10 min e vínculo ao usuário. A URL OAuth é validada
   antes que o browser abra o login oficial do Mercado Livre.
5. O seller **titular/administrador** aprova os escopos.
   O Mercado Livre redireciona ao callback fixo Next.js:
   `https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback`.
6. Next.js encaminha somente `code/state` ao callback FastAPI, que
   consome `state` uma única vez, troca o code e valida o perfil do seller.
   `access_token` e `refresh_token` são cifrados em
   `mercado_livre_connections`, isolados por `user_id`.
   A tabela `company_data_sources` registra `ml:<seller_id>` ativado.
7. Usuário retorna a `/integracoes?ml=connected`. O componente SyncAction
   inicia a importação do primeiro lote de anúncios e pedidos.
   O botão Atualizar dados percorre mais páginas, com limites explícitos.
8. O FastAPI renova os tokens automaticamente, com rotação do refresh
   sob `SELECT FOR UPDATE`, sem expor segredos ao browser.

## Vários clientes, vendedores e provedores
- Várias contas ML por tenant já são suportadas por
  `mercado_livre_connections` e por `account_id` explícito.
- `GET /api/v1/connectors/catalog` expõe metadados do catálogo.
  Mercado Livre está `available`; Mercado Pago, Shopee e TikTok Shop
  estão `planned`, **não conectados**. Outros provedores só serão
  liberados após APIs, credenciais, grants e testes reais.
- Mercado Pago exige credenciais de aplicativo separadas desde
  30/08/2026. Não reutilize Client ID/Secret do ML.
- Os dados de `products`, `orders` e `analytics` são sempre
  filtrados pela sessão FluxHub; `account_id` não substitui a
  verificação de propriedade.

## Configuração da conta desenvolvedora
No DevCenter ML, mantenha o aplicativo da SUA empresa, não um app por
cliente. Para leitura de anúncios, pedidos, métricas e notificações,
conceda **escopos funcionais mínimos necessários** e offline_access;
confira as permissões efetivas no painel do Mercado Livre.

Na VPS, configure de forma privada no FastAPI:
- MERCADO_LIVRE_CLIENT_ID e MERCADO_LIVRE_CLIENT_SECRET;
- MERCADO_LIVRE_REDIRECT_URI=https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback
  (exatamente o mesmo endereço no DevCenter);
- MERCADO_LIVRE_PKCE_ENABLED=true somente se PKCE estiver habilitado no app;
- DATABASE_URL, JWT_SECRET_KEY e TOKEN_ENCRYPTION_KEY já existentes;
- BACKEND_TOKEN idêntico em Next.js e FastAPI.

No Next.js:
- BACKEND_URL=http://127.0.0.1:8000
- BACKEND_TOKEN idêntico ao do FastAPI;
- AUTH_SESSION_SECRET (mínimo de 32 caracteres).

Atenção: o systemd desta VPS foi identificado com
`/var/www/fluxhub/backend/.env` e
`/etc/fluxhub-private/connection.env`. Se uma variável estiver em
ambos, o último EnvironmentFile define o valor carregado pelo systemd.
Não imprima segredos no terminal compartilhado.

## Publicar
1. Fazer backup do PostgreSQL e da configuração existente.
2. Atualizar backend e frontend separadamente com
   `git pull --ff-only origin main`.
3. Reiniciar FastAPI e reiniciar/rebuild Next.js conforme a configuração
   real do seu serviço. GitHub Actions **não** publica automaticamente.
4. Confirmar `GET /api/v1/health`, login FluxHub,
   `GET /api/v1/connectors/catalog` com autenticação e rota pública
   `/api/v1/connectors/mercado-livre/callback` no Next.js.
5. Em `/integracoes`, conectar uma conta vendedora no site oficial.
6. Consultar tabela sem exibir tokens:

   ```sql
   SELECT u.email, c.mercado_livre_user_id, c.nickname, c.expires_at
   FROM mercado_livre_connections c
   JOIN fluxhub_users u ON u.id = c.user_id;
   ```
7. Após autorização, validar a primeira sincronização e executar
   importação completa paginada para cobrir o catálogo.

### Erros comuns
- `invalid_grant`: code já utilizado/expirado, redirect ou PKCE incorreto;
- `invalid_operator_user_id`: seller colaborador em vez do titular;
- `invalid_client`: app secret/ID incorreto;
- `source_connected=false`: integração pausada/desconectada;
- `401` interno: BACKEND_TOKEN Next.js/FastAPI divergentes.

Não executamos testes `Base.metadata.drop_all` contra o banco de produção.
Documentação oficial: https://developers.mercadolivre.com.br/autenticacao-e-autorizacao
e https://developers.mercadolivre.com.br/pt_br/permissoes-funcionais/
