from app.connectors.mercado_livre.client import MercadoLivreClient
from app.schemas.order import Order, OrderItem


class MercadoLivreOrders:

    def __init__(self, client: MercadoLivreClient):
        self.client = client

    async def get_order(self, order_id: str) -> Order:
        data = await self.client.get(f"/orders/{order_id}")

        items = [
            OrderItem(
                product_id=item["product_id"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
            )
            for item in data["items"]
        ]

        return Order(
            external_id=data["id"],
            status=data["status"],
            total_amount=data["total_amount"],
            items=items,
        )