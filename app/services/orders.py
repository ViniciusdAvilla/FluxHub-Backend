from app.connectors.mercado_livre.orders import MercadoLivreOrders
from app.schemas.order import Order


class OrderService:

    def __init__(self, orders_connector: MercadoLivreOrders):
        self.orders_connector = orders_connector

    async def get_order(self, order_id: str) -> Order:
        return await self.orders_connector.get_order(order_id)