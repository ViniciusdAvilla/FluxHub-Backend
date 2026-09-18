from fastapi import APIRouter

router = APIRouter(
    prefix="/sales",
    tags=["Sales"]
)

@router.get("/")
async def get_sales():

    return [
        {
            "date": "2026-09-17",
            "revenue": 1549.70
        }
    ]