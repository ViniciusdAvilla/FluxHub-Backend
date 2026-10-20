from app.database.repositories.products import ProductRepository


class DashboardService:

    def __init__(
        self,
        repository: ProductRepository,
    ):
        self.repository = repository

    def get_dashboard(self):

        products = self.repository.get_all()

        total_products = len(products)
        if not total_products:
            return {
                "total_products": 0,
                "total_stock": 0,
                "avg_original_price": None,
                "avg_promotional_price": None,
                "avg_discount": None,
                "avg_rating": None,
            }

        def average(attribute: str):
            values = [getattr(product, attribute) for product in products]
            known = [value for value in values if value is not None]
            return round(sum(known) / len(known), 2) if known else None

        return {
            "total_products": total_products,

            "total_stock": (
                sum(p.stock_qty for p in products)
                if all(p.stock_qty is not None for p in products)
                else None
            ),
            "avg_original_price": average("original_price"),
            "avg_promotional_price": average("promotional_price"),
            "avg_discount": average("discount_rate"),
            "avg_rating": average("rating"),
        }