from abc import ABC, abstractmethod


class Connector(ABC):

    @abstractmethod
    async def get(self, endpoint: str):
        pass


class ProductSource(ABC):

    @abstractmethod
    def get_products(self) -> list:
        pass