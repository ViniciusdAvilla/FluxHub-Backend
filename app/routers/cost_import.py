"""Preview and confirm a tenant-scoped bulk purchase-cost document import."""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.models import UserModel
from app.database.session import get_db
from app.services.cost_documents import ALLOWED_EXTENSIONS
from app.services.cost_import import (
    MAX_COST_IMPORT_BYTES, CostImportError, analyze_cost_import,
    apply_cost_import, preview_token,
)

router = APIRouter(prefix="/pricing/cost-import", tags=["Cost Imports"])


async def _read_document(file: UploadFile) -> bytes:
    if not file.filename or Path(file.filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Envie CSV, Excel, Word (.docx), PDF, TXT ou TSV.")
    data = await file.read(MAX_COST_IMPORT_BYTES + 1)
    await file.close()
    if len(data) > MAX_COST_IMPORT_BYTES:
        raise HTTPException(413, "Arquivo maior que 16 MB.")
    return data


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await _read_document(file)
    try:
        result = analyze_cost_import(db, user.id, data, file.filename or "")
    except CostImportError as error:
        raise HTTPException(422, str(error)) from error
    return {
        "total": result["total"], "ready": result["ready"],
        "summary": result["summary"], "rows": result["rows"],
        "preview_limited": result["preview_limited"],
        "preview_token": preview_token(user.id, result),
        "file_sha": result["file_sha"],
        "note": "Preço Médio preenche apenas custos de compra ausentes. Custos já registrados, preços anunciados e estoque serão preservados.",
    }


@router.post("/apply")
async def apply(
    file: UploadFile = File(...),
    preview_token_value: str = Form(..., alias="preview_token"),
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await _read_document(file)
    try:
        return apply_cost_import(db, user.id, data, preview_token_value, file.filename or "")
    except CostImportError as error:
        raise HTTPException(422, str(error)) from error
