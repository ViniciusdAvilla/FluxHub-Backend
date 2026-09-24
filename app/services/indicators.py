from app.database.repositories.products import ProductRepository


class IndicatorService:

    def __init__(
        self,
        repository: ProductRepository,
    ):
        self.repository = repository

    def get_indicators(self):

        products = self.repository.get_all()

        total_products = len(products)

        avg_original_price = (
            sum(p.original_price for p in products)
            / total_products
        )

        avg_promotional_price = (
            sum(p.promotional_price for p in products)
            / total_products
        )

        avg_discount = (
            sum(p.discount_rate for p in products)
            / total_products
        )

        return {
            "total_products": total_products,
            "avg_original_price": round(avg_original_price, 2),
            "avg_promotional_price": round(avg_promotional_price, 2),
            "avg_discount": round(avg_discount * 100, 2),
        }