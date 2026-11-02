"""Fill missing Mercado-Livre average *purchase costs* from a CSV safely.

Never updates selling prices or stock from Mercado Livre. Matching is exact:
MLB identifier or seller SKU observed in the authenticated tenant's order lines.
Unknown/ambiguous/duplicate codes are skipped, never matched by title.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.models import (
    MercadoLivreListingSnapshotModel as Listing,
    MercadoLivreOrderLineModel as OrderLine,
    MercadoLivreProductCostModel as Cost,
)

MAX_COST_IMPORT_BYTES = 8 * 1024 * 1024
MAX_COST_IMPORT_ROWS = 30000
MAX_PREVIEW_ROWS = 100
MONEY_CAP = Decimal("1000000000.00")
MIN_REVIEW_COST = Decimal("0.01")


@dataclass(frozen=True)
class CostCsvRow:
    line: int
    code: str
    reference: str
    mlb: str
    name: str
    amount: Decimal | None
    issue: str | None = None


class CostImportError(ValueError):
    pass


def _header(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return re.sub(r"[^a-z0-9]", "", "".join(ch for ch in value
                  if not unicodedata.combining(ch)).lower())


def _key(value: object) -> str:
    # Retain punctuation and leading zeroes: these are significant in SKU matching.
    return str(value or "").strip().upper()


def _money(value: str) -> Decimal:
    plain = value.strip().replace("R$", "").replace("\u00a0", "").replace(" ", "")
    if not plain:
        raise ValueError("Preço médio vazio.")
    if "," in plain:
        plain = plain.replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", plain):
        raise ValueError("Preço médio inválido.")
    try:
        amount = Decimal(plain)
    except InvalidOperation as exc:
        raise ValueError("Preço médio inválido.") from exc
    if not amount.is_finite() or amount > MONEY_CAP:
        raise ValueError("Preço médio fora do limite.")
    return amount


def parse_csv(content: bytes) -> list[CostCsvRow]:
    if not content or len(content) > MAX_COST_IMPORT_BYTES:
        raise CostImportError("CSV vazio ou maior que 8 MB.")
    try:
        raw = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = content.decode("cp1252")
    if "\x00" in raw:
        raise CostImportError("O arquivo não parece ser um CSV.")
    first_line = raw.splitlines()[0] if raw.splitlines() else ""
    delimiter = ";" if ";" in first_line else ","
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter, strict=True)
    if not reader.fieldnames:
        raise CostImportError("Cabeçalho CSV ausente.")
    # Some ERP exports end each row with ';', leaving an unnamed empty column.
    named_headers = [name for name in reader.fieldnames if name and name.strip()]
    cols = {_header(name): name for name in named_headers}
    if len(cols) != len(named_headers):
        raise CostImportError("Cabeçalhos repetidos na planilha.")
    price_col = cols.get("precomedio")
    code_col = cols.get("coditem") or cols.get("codigoitem")
    ref_col = cols.get("referencia") or cols.get("sku")
    mlb_col = cols.get("mlb") or cols.get("idmercadolivre")
    name_col = cols.get("descricao") or cols.get("nomeproduto")
    if not price_col or not (code_col or ref_col or mlb_col):
        raise CostImportError("CSV precisa conter Preço Médio e Referência, Cód.Item ou MLB.")
    rows: list[CostCsvRow] = []
    try:
        for line, record in enumerate(reader, start=2):
            if len(rows) >= MAX_COST_IMPORT_ROWS:
                raise CostImportError("O CSV excede 30.000 linhas.")
            if None in record:
                raise CostImportError(f"Linha {line}: colunas extras; confira o separador.")
            code = _key(record.get(code_col)) if code_col else ""
            reference = _key(record.get(ref_col)) if ref_col else ""
            mlb = _key(record.get(mlb_col)) if mlb_col else ""
            name = (record.get(name_col) or "").strip()[:160] if name_col else ""
            try:
                amount = _money(record.get(price_col) or "")
                issue = ("revisar_valor" if amount <= MIN_REVIEW_COST else None)
            except ValueError:
                amount, issue = None, "preco_invalido"
            if not code and not reference and not mlb:
                issue = "sem_identificador"
            rows.append(CostCsvRow(line, code, reference, mlb, name, amount, issue))
    except csv.Error as exc:
        raise CostImportError("CSV malformado; confira aspas e separadores.") from exc
    if not rows:
        raise CostImportError("Nenhum produto encontrado no CSV.")
    return rows


def _current_catalog(db: Session, user_id: int) -> dict[str, tuple[str, str]]:
    result = {}
    for item_id, title in db.execute(select(Listing.external_id, Listing.title).where(
        Listing.user_id == user_id)):
        result[_key(item_id)] = (item_id, title)
    return result


def _sku_map(db: Session, user_id: int, wanted: set[str],
             available: dict[str, tuple[str, str]]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = defaultdict(set)
    if not wanted:
        return mapping
    # Single indexed tenant scan (never all sellers), with bounded streaming results.
    rows = db.execute(select(OrderLine.sku, OrderLine.item_id).where(
        OrderLine.user_id == user_id, OrderLine.sku.is_not(None)))
    for sku, item_id in rows.yield_per(2000):
        key = _key(sku)
        if key in wanted and _key(item_id) in available:
            mapping[key].add(_key(item_id))
    return mapping


def analyze_cost_import(db: Session, user_id: int, content: bytes) -> dict:
    rows = parse_csv(content)
    available = _current_catalog(db, user_id)
    wanted = {key for row in rows for key in (row.reference, row.code) if key}
    sku_map = _sku_map(db, user_id, wanted, available)
    classified: list[dict] = []

    for row in rows:
        result = {
            "line": row.line, "code": row.code, "reference": row.reference,
            "description": row.name, "new_cost": float(row.amount) if row.amount is not None else None,
            "item_id": None, "matched_title": None, "old_cost": None,
            "match_type": None, "status": row.issue or "",
        }
        if row.issue:
            classified.append(result)
            continue
        matches: set[str] = set()
        if row.mlb:
            if row.mlb in available:
                matches.add(row.mlb)
                result["match_type"] = "mlb"
        else:
            by_ref = sku_map.get(row.reference, set()) if row.reference else set()
            by_code = sku_map.get(row.code, set()) if row.code else set()
            # An MLB supplied as Referência (common in older ERP exports) is
            # still an exact tenant-owned listing ID, not a fuzzy title match.
            by_id = {key for key in (row.reference, row.code)
                     if re.fullmatch(r"MLB\d+", key) and key in available}
            matches = set(by_ref) | set(by_code) | by_id
            if matches:
                result["match_type"] = "mlb" if by_id and not (set(by_ref) | set(by_code)) else "sku"
        if len(matches) > 1:
            result["status"] = "ambiguo"
        elif not matches:
            result["status"] = "nao_encontrado"
        else:
            item_key = next(iter(matches))
            item_id, title = available[item_key]
            result["item_id"] = item_id
            result["matched_title"] = title
            result["status"] = "candidato"
        classified.append(result)

    # Two rows targeting the same MLB must never silently overwrite each other.
    counts = Counter(item["item_id"] for item in classified if item["item_id"])
    for item in classified:
        if item["item_id"] and counts[item["item_id"]] > 1:
            item["status"] = "duplicado"
    costs = {item_id: value for item_id, value in db.execute(select(
        Cost.external_id, Cost.unit_cost).where(Cost.user_id == user_id))}
    changes = []
    for item in classified:
        if item["status"] not in ("candidato", "duplicado"):
            continue
        old = costs.get(item["item_id"])
        item["old_cost"] = old
        if item["status"] == "duplicado":
            continue
        # Only fill missing costs. Any positive previously saved cost is immutable
        # through the CSV importer, even when it differs from the spreadsheet.
        item["status"] = "sem_alteracao" if (
            old is not None and Decimal(str(old)) > 0
        ) else "atualizar"
        if item["status"] == "atualizar":
            changes.append((item["item_id"], str(item["new_cost"]), str(old) if old is not None else None))
    states = Counter(item["status"] for item in classified)
    fingerprint = hashlib.sha256(json.dumps(sorted(changes),
        separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return {
        "file_sha": hashlib.sha256(content).hexdigest(),
        "fingerprint": fingerprint,
        "changes": changes,
        "total": len(rows),
        "ready": len(changes),
        "summary": {
            "atualizar": states["atualizar"], "sem_alteracao": states["sem_alteracao"],
            "nao_encontrado": states["nao_encontrado"],
            "ambiguo": states["ambiguo"], "duplicado": states["duplicado"],
            "revisar_valor": states["revisar_valor"],
            "preco_invalido": states["preco_invalido"],
            "sem_identificador": states["sem_identificador"],
        },
        "rows": _representative_rows(classified),
        "preview_limited": len(classified) > MAX_PREVIEW_ROWS,
    }


def _representative_rows(rows: list[dict]) -> list[dict]:
    """Show a bounded cross-section so matches aren't hidden by early placeholder rows."""
    quotas = {
        "atualizar": 35, "sem_alteracao": 10, "nao_encontrado": 15,
        "ambiguo": 10, "duplicado": 10, "revisar_valor": 10,
        "preco_invalido": 5, "sem_identificador": 5,
    }
    picked: list[dict] = []
    picked_lines: set[int] = set()
    for status, limit in quotas.items():
        for row in rows:
            if row["status"] == status and limit:
                picked.append(row)
                picked_lines.add(row["line"])
                limit -= 1
            if not limit:
                break
    for row in rows:
        if len(picked) >= MAX_PREVIEW_ROWS:
            break
        if row["line"] not in picked_lines:
            picked.append(row)
    return sorted(picked, key=lambda item: item["line"])


def _signing_key() -> bytes:
    secret = settings.jwt_secret_key
    if not secret:
        raise HTTPException(503, "Assinatura da prévia indisponível.")
    return secret.get_secret_value().encode("utf-8")


def preview_token(user_id: int, result: dict) -> str:
    data = {
        "version": 2, "uid": user_id, "file": result["file_sha"],
        "fp": result["fingerprint"], "expiry": int(time.time()) + 900,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(data, sort_keys=True,
        separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def verify_token(user_id: int, content: bytes, provided: str, result: dict) -> None:
    if len(provided) > 2000 or "." not in provided:
        raise HTTPException(400, "Prévia inválida; envie o arquivo novamente.")
    encoded, signature = provided.split(".", 1)
    expected = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(400, "Assinatura da prévia inválida.")
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Prévia inválida; envie o arquivo novamente.") from None
    if (data.get("version") != 2 or data.get("uid") != user_id
        or data.get("file") != result["file_sha"]
        or data.get("fp") != result["fingerprint"] or data.get("expiry", 0) < time.time()):
        raise HTTPException(409, "Arquivo ou custos mudaram; refaça a prévia.")
    if result["ready"] < 1:
        raise HTTPException(422, "Nenhum custo ausente disponível para preenchimento.")


def apply_cost_import(db: Session, user_id: int, content: bytes, token: str) -> dict:
    # Serialize concurrent imports for a tenant before rechecking the preview.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": 8934700000 + user_id})
    db.execute(select(Cost.id).where(Cost.user_id == user_id).with_for_update()).all()
    result = analyze_cost_import(db, user_id, content)
    verify_token(user_id, content, token, result)
    current = {item_id: row for item_id, row in db.execute(select(Cost.external_id, Cost).where(
        Cost.user_id == user_id))}
    added = 0
    filled_zero = 0
    for item_id, new_cost, _old in result["changes"]:
        cost = current.get(item_id)
        if cost is None:
            db.add(Cost(
                user_id=user_id, external_id=item_id,
                unit_cost=float(new_cost), manufacturer="",
                tax_percent=0, shipping_cost=0, other_cost=0,
            ))
            added += 1
        elif cost.unit_cost is None or cost.unit_cost <= 0:
            # Defensive check in addition to the signed preview / row locks.
            cost.unit_cost = float(new_cost)
            filled_zero += 1
    db.commit()
    filled = added + filled_zero
    return {
        "updated": filled, "filled": filled,
        "new_cost_records": added, "filled_zero_costs": filled_zero,
        "unchanged": result["summary"]["sem_alteracao"],
        "not_matched": result["summary"]["nao_encontrado"],
        "skipped": result["total"] - filled - result["summary"]["sem_alteracao"],
        "message": "Custos ausentes preenchidos. Custos existentes, preços anunciados e estoques preservados.",
    }
