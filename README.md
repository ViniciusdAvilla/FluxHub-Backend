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