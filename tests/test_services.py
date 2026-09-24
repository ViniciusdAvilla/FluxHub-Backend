import pytest

from app.connectors.mercado_livre.products import MercadoLivreProducts
from app.connectors.mercado_livre.orders import MercadoLivreOrders

from app.services.products import ProductService
from app.services.orders import OrderService


class FakeMercadoLivreProductClient:

    async def get(self, endpoint: str):

        return {
            "id": "MLB123",
            "title": "Produto teste",
            "category": "Ferramentas",
            "brand": "Gedore",
            "original_price": 120.00,
            "promotional_price": 100.50,
            "discount_rate": 0.16,
            "stock_qty": 50,
            "sales_30d": 20,
            "rating": 4.8,
            "is_free_shipping": True,
            "item_condition": "Novo",
        }


class FakeMercadoLivreOrderClient:

    async def get(self, endpoint: str):
        return {
            "id": "2000001234567890",
            "status": "paid",
            "total_amount": 349.90,
            "items": [
                {
                    "product_id": "MLB001",
                    "quantity": 2,
                    "unit_price": 100.00,
                },
                {
                    "product_id": "MLB002",
                    "quantity": 1,
                    "unit_price": 149.90,
                },
            ],
        }


@pytest.mark.asyncio
async def test_product_service():

    client = FakeMercadoLivreProductClient()
    connector = MercadoLivreProducts(client)
    service = ProductService(connector)

    product = await service.get_product("MLB123")

    assert product.external_id == "MLB123"
    assert product.title == "Produto teste"
    assert product.promotional_price == 100.50


@pytest.mark.asyncio
async def test_order_service():

    client = FakeMercadoLivreOrderClient()
    connector = MercadoLivreOrders(client)
    service = OrderService(connector)

    order = await service.get_order("2000001234567890")

    assert order.external_id == "2000001234567890"
    assert order.status == "paid"
    assert order.total_amount == 349.90

    assert len(order.items) == 2

    assert order.items[0].product_id == "MLB001"
    assert order.items[0].quantity == 2
    assert order.items[0].unit_price == 100.00

    assert order.items[1].product_id == "MLB002"
    assert order.items[1].quantity == 1
    assert order.items[1].unit_price == 149.90