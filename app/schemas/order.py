from pydantic import BaseModel


class OrderItem(BaseModel):
    product_id: str
    quantity: int
    unit_price: float


class Order(BaseModel):
    external_id: str
    status: str
    total_amount: float
    items: list[OrderItem]