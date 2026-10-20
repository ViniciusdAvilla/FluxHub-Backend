import httpx

from app.connectors.base.connector import Connector


class MercadoLivreClient(Connector):

    BASE_URL = "https://api.mercadolibre.com"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token

    async def get(self, endpoint: str):
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}{endpoint}",
                headers=headers,
            )

        response.raise_for_status()

        return response.json()