from pydantic import BaseModel


class Product(BaseModel):
    external_id: str
    title: str
    category: str
    brand: str
    original_price: float
    promotional_price: float
    discount_rate: float
    stock_qty: int
    sales_30d: int
    rating: float
    is_free_shipping: bool
    item_condition: str