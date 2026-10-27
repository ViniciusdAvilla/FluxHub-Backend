# FluxHub API

## Mercado Livre OAuth

Each FluxHub login represents one company. Administrators create user accounts;
there is no public registration endpoint. A company can connect multiple
Mercado Livre seller accounts and Excel product sources. Every source has an
independent enabled switch; disabling one excludes its products from
consolidated catalog indicators without deleting its history.

### Configuration

Copy `.env.example` to `.env` (or set the same variables in the VPS service
environment). Replace the database placeholder and configure the Mercado Livre
App ID and Secret Key without committing them. Generate the signing and token
encryption keys:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the first value as `JWT_SECRET_KEY` and the second as
`TOKEN_ENCRYPTION_KEY`. Back up the encryption key securely: saved OAuth tokens
cannot be decrypted if this key is lost or changed.

Set `FRONTEND_URL` to the public HTTPS URL of the Next.js application. The
configured `MERCADO_LIVRE_REDIRECT_URI` must be registered exactly in the
Mercado Livre developer application and must be publicly reachable over HTTPS.
OAuth will not complete until the callback is accessible.

Install dependencies and apply the database migration:

```powershell
pip install -r requirements.txt
alembic upgrade head
```

Create each FluxHub user from the VPS or an administrative environment:

```powershell
python -m scripts.create_user
```

Start the API with `uvicorn app.main:app`. The Next.js application proxies
`POST /api/v1/auth/login` and stores the resulting JWT in an HttpOnly cookie;
the browser does not receive the Mercado Livre app secret or a bearer token.
OAuth tokens are encrypted before storage and seller accounts remain linked to
their company. Use Alembic (`alembic upgrade head`) for schema changes; do not
run `create_all` first on a new deployment.

The authenticated company-source endpoints are:

- `GET /api/v1/connectors` — source status and supported providers.
- `PATCH /api/v1/connectors/{provider}/{account_key}` — include/exclude a
  connected source from indicators.
- `POST /api/v1/products/import/excel` — import a validated `.xlsx` catalog,
  up to 15 MB and 10,000 rows.
- `GET /api/v1/products/` — only products from enabled sources for the signed-in
  company.
- `GET /api/v1/dashboard` — company-scoped dashboard indicators.

The Next.js connectors screen is `/connectors`; the frontend project is
`../dashboard-mercado-livre-raio-x`.

## Marketplace analytics endpoints

All analytics endpoints require the FluxHub bearer token returned by
`POST /api/v1/auth/login`:

- `POST /api/v1/analytics/sync?account_id={seller_id}` refreshes Mercado Livre
  listings, 30-day item visits, and orders/cancellations for a selected seller
  account. The account ID can be omitted when the company has one connection.
- `GET /api/v1/analytics/ads-performance` reports active listings, status
  counts, visits, listing age, price average, and a low-relevance heuristic
  (zero sales and below-average visits).
- `GET /api/v1/analytics/cancellations?days=30` summarizes purchases and
  cancelled listings where an explicit cancellation event is available.
- `GET /api/v1/analytics/financial-monitoring?days=30` reports active listing
  value, listings priced over 20% above their category peers, below-category
  prices, and canceled-order value.
- `GET /api/v1/analytics/ads-profitability?days=30` calculates Ads profitability
  from imported Mercado Ads daily reports and product costs.
- `GET /api/v1/analytics/product-costs` and
  `PUT /api/v1/analytics/product-costs/{item_id}` read/save company-specific
  product costs and manufacturer labels in PostgreSQL. For example:

```json
{
  "unit_cost": 42.5,
  "marketplace_fee_percent": 16,
  "manufacturer": "Fabricante exemplo",
  "tax_percent": 6,
  "shipping_cost": 5,
  "other_cost": 2
}
```
- `POST /api/v1/analytics/product-costs/import` imports up to 500 costs at once
  from a JSON list (the spreadsheet screen can map its rows to this shape):

```json
{
  "items": [
    {
      "external_id": "MLB123456789",
      "unit_cost": 42.5,
      "marketplace_fee_percent": 16,
      "manufacturer": "Fabricante exemplo",
      "tax_percent": 6,
      "shipping_cost": 5,
      "other_cost": 2
    }
  ]
}
```

The product cost and marketplace fee are account-specific inputs. Net
profitability is marked incomplete until every attributed Ads sale has both.
ROAS is attributed Ads revenue divided by Ads spend; `real_roas` is profit after
product cost, marketplace fee, and Ads spend divided by Ads spend. A missing
spend returns `null` instead of dividing by zero.

The current seller API does not return identities or the individual shopping
carts of potential buyers. The performance response marks this information as
unavailable; it must not be represented as a list of customers. Likewise,
closing/pausing an item is not by itself proof that a listing was cancelled.
Listing-cancellation counts remain unavailable unless the platform provides an
explicit cancellation event.

Mercado Ads reporting requires a separate, enabled Product Ads API permission
and its reports must be synchronized into the daily Ads snapshots. The current
sync endpoint deliberately does not invent Ads spend/sales or seller listing
cancellation data when the upstream API does not provide it. Until the OAuth
callback is reachable and the app grants the needed Ads permission, these
responses report `has_ads_data: false`.

---

> **Nota de manutenção:** esta documentação registra módulos OAuth e analytics que podem não estar disponíveis nesta branch. Consulte as rotas realmente incluídas em `app/main.py` antes de usar seus exemplos.

# FluxHub Backend — segurança da API

Este FastAPI requer um **token de serviço** para todas as rotas, incluindo `/api/v1/products/`, `/docs` e `/openapi.json`. Apenas `GET /api/v1/health` permanece público para monitoramento (responde apenas `{"status":"online"}`).

## Configurar o token nos DOIS serviços

Gere uma chave aleatória (não use sua senha de login):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Configure **o mesmo valor** em ambiente privado, com o nome `BACKEND_TOKEN`:

- **FastAPI:** `BACKEND_TOKEN` usado pelo middleware de autenticação.
- **Next.js:** `BACKEND_TOKEN` usado em `Authorization: Bearer ...` nas chamadas privadas ao FastAPI.

Não faça commit do segredo, não envie ao navegador e **não use `NEXT_PUBLIC_BACKEND_TOKEN`**. O token deve ter no mínimo 32 caracteres, sem espaços nas extremidades. Sem token válido, a API retorna `503` (configuração ausente) ou `401` (cabeçalho inválido).

Configure a variável antes de implantar/reiniciar o backend; o FastAPI lê as variáveis na inicialização.

## Compatibilidade de aplicação

Os dois comandos usam a mesma instância protegida:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# ou
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

O frontend já consegue adicionar esse cabeçalho somente pelo Next.js. Para testar diretamente sem revelar o segredo a outras pessoas:

```bash
# Defina BACKEND_TOKEN no terminal de forma privada.
curl -i http://127.0.0.1:8000/api/v1/products/
curl -i -H "Authorization: Bearer $BACKEND_TOKEN" http://127.0.0.1:8000/api/v1/products/
curl -i -H "Authorization: Bearer $BACKEND_TOKEN" http://127.0.0.1:8000/openapi.json
```

A primeira deve retornar 401, as outras 200 quando o banco e a API estiverem disponíveis. O endereço público com o prefixo `/backend-api` pode ser usado se o proxy remove esse prefixo ao encaminhar ao FastAPI.

**Swagger protegido:** a URL `/docs` não abre diretamente no navegador sem `Authorization`. Isso evita a exposição pública da documentação. Para testar endpoints, use o OpenAPI por curl/Postman com token, ou consulte os endpoints GET na Central de Dados do FluxHub.

## Implantação segura

1. Configure `BACKEND_TOKEN` primeiro no ambiente do Next.js e no ambiente FastAPI, com o mesmo valor, e atualize a versão do frontend que exige token.
2. Publique o backend protegido. Faça redeploy/reinicie os dois serviços.
3. Verifique que o navegador não consegue acessar diretamente `/backend-api/api/v1/products/`, `/backend-api/docs` e `/backend-api/openapi.json` sem token, mas que a Central de Dados consegue consultar.
4. Restrinja o FastAPI com firewall/Nginx às redes/servidores necessários. Se usar o endereço público `http://187.77.230.250`, configure **HTTPS/TLS** antes de transportar tokens reais. HTTP transmite o bearer token sem criptografia.

**Importante:** o token de serviço não substitui a autenticação do usuário no Next.js. A senha humana fica fora do backend, e a conta do frontend é configurada separadamente por hash/sessão.

## Testes

```bash
pip install -r requirements.txt
pytest tests/test_api_auth.py -q
```


## Sincronização integral do Mercado Livre (jobs persistidos)

Não há mais teto artificial de 3.000 anúncios no PostgreSQL. A API grava
anúncios/produtos encontrados pelo scan/scroll em lotes pequenos; os pedidos
são consultados em janelas de data, com paginação interna e janelas menores
quando a contagem se aproxima de 9.000 resultados. As operações usam upsert
e não apagam registros anteriores. Erros temporários/429 têm espera e retomada;
a continuidade não depende de uma aba do navegador aberta.

**Antes do primeiro deploy** faça backup do PostgreSQL e rode a migração
aditiva no ambiente virtual e diretório do backend (com DATABASE_URL válida):

```bash
python -m scripts.migrate_sync_jobs
```

A migração cria apenas `mercado_livre_sync_jobs` e não remove anúncios,
pedidos ou tabelas existentes. Faça o deploy **do backend primeiro**; só então
publique o frontend, que chama as novas rotas:

- `POST /api/v1/analytics/sync-jobs?account_id=123` cria/retoma uma execução
  autenticada para uma conta da empresa, retornando HTTP 202.
- `GET /api/v1/analytics/sync-jobs/123` informa fase, status, anúncios
  consultados/salvos, pedidos, páginas, retentativas e datas.
- `GET /api/v1/analytics/order-status?days=30&account_id=123` agrupa os pedidos
  da empresa usando **todo** o banco, não só os 100 mais recentes.
- `GET /api/v1/orders/?days=30&limit=100&offset=0` serve páginas de pedidos.

`SYNC_WORKER_ENABLED=true` é o padrão no processo da API; ele varre a fila
de jobs a partir de seu próprio loop async. Uma instalação com muitos
processos pode mantê-lo habilitado: leases no PostgreSQL e
`FOR UPDATE SKIP LOCKED` evitam que o mesmo job seja assumido duas vezes.
Após uma queda, o lease expira em até 10 minutos e o job é reprocessado
seguramente por identificador. Para suspender o processamento sem alterar os
dados, configure `SYNC_WORKER_ENABLED=false` e reinicie o serviço.

### Diagnóstico na VPS

```bash
sudo journalctl -u fluxhub-backend -n 100 --no-pager
```

No `psql`, conectado à mesma base que consta em `DATABASE_URL`:

```sql
SELECT user_id, mercado_livre_user_id, status, phase,
       listings_scanned, listings_saved, orders_saved, pages_done,
       retry_count, retry_at, updated_at, completed_at
FROM mercado_livre_sync_jobs
ORDER BY updated_at DESC;
```

`completed` significa que foram percorridos os anúncios e pedidos
**disponíveis para o aplicativo e vendedor na API**. A própria documentação
Mercado Livre estabelece disponibilidade de pedidos de até 12 meses e
limites de requisição. Informações sem permissão/endpoint autorizado
(ex.: relatórios Product Ads, informações financeiras Mercado Pago) não são
inventadas nem marcadas como sincronizadas pela rotina.
