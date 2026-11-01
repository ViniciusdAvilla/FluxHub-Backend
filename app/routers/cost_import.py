"""Preview and confirm a tenant-scoped average purchase-cost CSV import."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import UserModel
from app.database.session import get_db
from app.services.cost_import import (
    MAX_COST_IMPORT_BYTES, CostImportError, analyze_cost_import,
    apply_cost_import, preview_token,
)

router = APIRouter(prefix="/pricing/cost-import", tags=["Cost Imports"])


async def _read_csv(file: UploadFile) -> bytes:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(415, "Envie um arquivo CSV.")
    data = await file.read(MAX_COST_IMPORT_BYTES + 1)
    await file.close()
    if len(data) > MAX_COST_IMPORT_BYTES:
        raise HTTPException(413, "Arquivo maior que 8 MB.")
    return data


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await _read_csv(file)
    try:
        result = analyze_cost_import(db, user.id, data)
    except CostImportError as error:
        raise HTTPException(422, str(error)) from error
    return {
        "total": result["total"], "ready": result["ready"],
        "summary": result["summary"], "rows": result["rows"],
        "preview_limited": result["preview_limited"],
        "preview_token": preview_token(user.id, result),
        "file_sha": result["file_sha"],
        "note": "Preço Médio será cadastrado apenas como custo unitário. Anúncios e estoques não serão alterados.",
    }


@router.post("/apply")
async def apply(
    file: UploadFile = File(...),
    preview_token_value: str = Form(..., alias="preview_token"),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await _read_csv(file)
    try:
        return apply_cost_import(db, user.id, data, preview_token_value)
    except CostImportError as error:
        raise HTTPException(422, str(error)) from error
