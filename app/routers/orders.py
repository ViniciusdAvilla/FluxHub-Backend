from fastapi import APIRouter

from app.connectors.mercado_livre.orders import MercadoLivreOrders
from app.schemas.order import Order
from app.services.orders import OrderService


router = APIRouter(
    prefix="/orders",
    tags=["Orders"]
)


class FakeMercadoLivreOrderClient:

    async def get(self, endpoint: str):
        return {
            "id": "2000001234567890",
            "status": "paid",
            "total_amount": 349.90,
        }


@router.get("/{order_id}", response_model=Order)
async def get_order(order_id: str):

    client = FakeMercadoLivreOrderClient()

    connector = MercadoLivreOrders(client)

    service = OrderService(connector)

    return await service.get_order(order_id)


@router.get("/")
async def list_orders():
    return [
        {
            "external_id": "2000001",
            "status": "paid",
            "total_amount": 349.90
        },
        {
            "external_id": "2000002",
            "status": "shipped",
            "total_amount": 199.90
        }
    ]