import pytest

from app.connectors.mercado_livre.products import MercadoLivreProducts


class FakeMercadoLivreClient:

    async def get(self, endpoint: str):

        return {
            "id": "MLB123",
            "title": "Produto teste",
            "category": "Freios",
            "brand": "Bosch",
            "original_price": 220.00,
            "promotional_price": 199.90,
            "discount_rate": 0.09,
            "stock_qty": 50,
            "sales_30d": 20,
            "rating": 4.8,
            "is_free_shipping": True,
            "item_condition": "Novo",
        }


@pytest.mark.asyncio
async def test_get_product():

    client = FakeMercadoLivreClient()

    products = MercadoLivreProducts(client)

    product = await products.get_product("MLB123")

    assert product.external_id == "MLB123"
    assert product.title == "Produto teste"
    assert product.promotional_price == 199.90
    assert product.brand == "Bosch"
    assert product.stock_qty == 50