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
        if not total_products:
            return {
                "total_products": 0,
                "avg_original_price": None,
                "avg_promotional_price": None,
                "avg_discount": None,
            }

        def average(values):
            known = [value for value in values if value is not None]
            return sum(known) / len(known) if known else None

        avg_original_price = average([p.original_price for p in products])
        avg_promotional_price = average([p.promotional_price for p in products])
        avg_discount = average([p.discount_rate for p in products])

        return {
            "total_products": total_products,
            "avg_original_price": round(avg_original_price, 2)
            if avg_original_price is not None
            else None,
            "avg_promotional_price": round(avg_promotional_price, 2)
            if avg_promotional_price is not None
            else None,
            "avg_discount": round(avg_discount * 100, 2)
            if avg_discount is not None
            else None,
        }