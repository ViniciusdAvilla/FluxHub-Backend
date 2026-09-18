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

        return {
            "total_products": total_products,

            "total_stock": sum(
                p.stock_qty
                for p in products
            ),

            "avg_original_price": round(
                sum(
                    p.original_price
                    for p in products
                ) / total_products,
                2
            ),

            "avg_promotional_price": round(
                sum(
                    p.promotional_price
                    for p in products
                ) / total_products,
                2
            ),

            "avg_discount": round(
                (
                    sum(
                        p.discount_rate
                        for p in products
                    ) / total_products
                ) * 100,
                2
            ),

            "avg_rating": round(
                sum(
                    p.rating
                    for p in products
                ) / total_products,
                2
            ),
        }