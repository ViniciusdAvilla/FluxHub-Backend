from app.connectors.mercado_livre.client import MercadoLivreClient
from app.schemas.product import Product


class MercadoLivreProducts:

    def __init__(self, client: MercadoLivreClient):
        self.client = client

    async def get_product(self, product_id: str) -> Product:
        data = await self.client.get(f"/items/{product_id}")

        return Product(
            external_id=data["id"],
            title=data["title"],
            category=data["category"],
            brand=data["brand"],
            original_price=data["original_price"],
            promotional_price=data["promotional_price"],
            discount_rate=data["discount_rate"],
            stock_qty=data["stock_qty"],
            sales_30d=data["sales_30d"],
            rating=data["rating"],
            is_free_shipping=data["is_free_shipping"],
            item_condition=data["item_condition"],
        )