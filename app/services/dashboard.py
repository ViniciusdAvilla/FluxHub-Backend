"""Lightweight dashboard metrics from tenant-isolated catalog aggregates."""
from app.database.repositories.products import ProductRepository


class DashboardService:
    def __init__(self, repository: ProductRepository):
        self.repository = repository

    def get_dashboard(self):
        summary = self.repository.get_catalog_summary()
        def rounded(name: str):
            value = summary[name]
            return round(value, 2) if value is not None else None

        return {
            "total_products": summary["total_products"],
            "total_stock": (
                summary["total_stock_known"]
                if summary["unknown_stock_items"] == 0 else None
            ),
            "avg_original_price": rounded("avg_original_price"),
            "avg_promotional_price": rounded("avg_promotional_price"),
            "avg_discount": rounded("avg_discount"),
            "avg_rating": rounded("avg_rating"),
        }
