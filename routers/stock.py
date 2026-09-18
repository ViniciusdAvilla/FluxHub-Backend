from fastapi import APIRouter

router = APIRouter(
    prefix="/stock",
    tags=["Stock"]
)

@router.get("/")
async def get_stock():

    return [
        {
            "product_id": "MLB001",
            "available_quantity": 20
        },
        {
            "product_id": "MLB002",
            "available_quantity": 8
        }
    ]