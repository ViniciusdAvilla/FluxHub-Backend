import httpx

from app.connectors.base.connector import Connector


class MercadoLivreClient(Connector):

    BASE_URL = "https://api.mercadolibre.com"

    async def get(self, endpoint: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}{endpoint}"
            )

        response.raise_for_status()

        return response.json()