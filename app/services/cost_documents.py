"""Parse cost tables from common business files without guessing product identities.

Formats: CSV, TSV, XLSX, XLS, DOCX, and text-based PDF. Only explicitly
identified purchase-cost columns are read. No stock, sale-price or supplier
fields can be changed by the resulting rows.
"""
import csv
import io
import math
import re
import zipfile
from decimal import Decimal
from pathlib import Path

from app.services.cost_import import (
    CostCsvRow, CostImportError, MAX_COST_IMPORT_BYTES, MAX_COST_IMPORT_ROWS,
    MIN_REVIEW_COST, _header, _key, _money,
)

ALLOWED_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx", ".xls", ".docx", ".pdf", ".txt"})
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_PDF_PAGES = 250
MAX_TEXT_LENGTH = 6 * 1024 * 1024

COST_COLUMNS = (
    "precomedio", "customedio", "customediodecompra", "custodecompra",
    "custounitario", "custounit", "custo", "precocusto", "precodecusto",
    "precodecompra", "valorcusto", "valordecusto", "valorcompra",
    "unitcost", "purchasecost",
)
CODE_COLUMNS = ("coditem", "codigoitem", "codigoproduto", "codigo", "cod")
REFERENCE_COLUMNS = ("referencia", "ref", "sku", "skuvendedor", "sellercustomfield")
MLB_COLUMNS = ("mlb", "idmercadolivre", "idanuncio", "idmlb")
NAME_COLUMNS = ("descricao", "nomeproduto", "produto", "descricaoitem", "nome")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return ""
        if float(value).is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip()


def _cost(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Custo inválido.")
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount < 0 or amount > Decimal("1000000000.00"):
                raise ValueError("Custo inválido.")
            # Ignore insignificant Excel numeric precision after two decimals.
            if amount != amount.quantize(Decimal("0.01")):
                raise ValueError("Custo com mais de duas casas decimais.")
            return amount
        except (ArithmeticError, ValueError) as exc:
            raise ValueError("Custo inválido.") from exc
    return _money(_cell(value))


def _find_header(row: list[object]) -> dict[str, int] | None:
    columns: dict[str, int] = {}
    for i, value in enumerate(row):
        key = _header(_cell(value))
        if key and key not in columns:
            columns[key] = i

    def find(aliases: tuple[str, ...]) -> int | None:
        for key in aliases:
            if key in columns:
                return columns[key]
        return None

    price = find(COST_COLUMNS)
    code = find(CODE_COLUMNS)
    reference = find(REFERENCE_COLUMNS)
    mlb = find(MLB_COLUMNS)
    if price is None or (code is None and reference is None and mlb is None):
        return None
    result = {"cost": price}
    for key, index in (("code", code), ("reference", reference),
                       ("mlb", mlb), ("name", find(NAME_COLUMNS))):
        if index is not None:
            result[key] = index
    return result


def _row_value(row: list[object], header: dict[str, int], field: str) -> object:
    index = header.get(field)
    return row[index] if index is not None and index < len(row) else None


def _record(row: list[object], header: dict[str, int], line: int) -> CostCsvRow | None:
    if not any(_cell(value) for value in row):
        return None
    code = _key(_cell(_row_value(row, header, "code")))
    reference = _key(_cell(_row_value(row, header, "reference")))
    mlb = _key(_cell(_row_value(row, header, "mlb")))
    name = _cell(_row_value(row, header, "name"))[:160]
    try:
        amount = _cost(_row_value(row, header, "cost"))
        issue = "revisar_valor" if amount <= MIN_REVIEW_COST else None
    except (ValueError, ArithmeticError):
        amount, issue = None, "preco_invalido"
    if not code and not reference and not mlb:
        issue = "sem_identificador"
    return CostCsvRow(line, code, reference, mlb, name, amount, issue)


def _append_rows(target: list[CostCsvRow], table) -> None:
    header: dict[str, int] | None = None
    for row in table:
        if not isinstance(row, list):
            row = list(row)
        candidate = _find_header(row)
        if candidate is not None:
            header = candidate
            continue
        if header is None:
            continue
        parsed = _record(row, header, len(target) + 2)
        if parsed is None:
            continue
        if len(target) >= MAX_COST_IMPORT_ROWS:
            raise CostImportError("O documento excede 30.000 produtos.")
        target.append(parsed)


def _validated_zip(content: bytes) -> None:
    if not content.startswith(b"PK"):
        raise CostImportError("Arquivo Excel/Word inválido.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES or sum(x.file_size for x in entries) > MAX_UNCOMPRESSED_BYTES:
                raise CostImportError("Documento compactado muito grande.")
            if any(x.flag_bits & 1 for x in entries):
                raise CostImportError("Documentos protegidos por senha não são aceitos.")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise CostImportError("Documento Excel/Word danificado.") from exc


def _parse_xlsx(content: bytes) -> list[CostCsvRow]:
    from openpyxl import load_workbook
    _validated_zip(content)
    rows: list[CostCsvRow] = []
    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            for sheet in book.worksheets:
                if sheet.sheet_state != "visible":
                    continue
                _append_rows(rows, (list(line) for line in sheet.iter_rows(values_only=True)))
        finally:
            book.close()
    except CostImportError:
        raise
    except Exception as exc:
        raise CostImportError("Não foi possível ler as tabelas do Excel.") from exc
    return rows


def _parse_xls(content: bytes) -> list[CostCsvRow]:
    import xlrd
    if not content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise CostImportError("Arquivo .xls inválido.")
    try:
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
        rows: list[CostCsvRow] = []
        try:
            for sheet in book.sheets():
                if sheet.visibility != 0:
                    continue
                _append_rows(rows, (sheet.row_values(i) for i in range(sheet.nrows)))
        finally:
            book.release_resources()
    except CostImportError:
        raise
    except Exception as exc:
        raise CostImportError("Não foi possível ler o Excel antigo (.xls).") from exc
    return rows


def _parse_delimited(content: bytes, suffix: str) -> list[CostCsvRow]:
    raw = _decode_text(content)
    first_line = next((line for line in raw.splitlines() if line.strip()), "")
    separator = "\t" if suffix == ".tsv" else (";" if ";" in first_line else ",")
    try:
        table = list(csv.reader(io.StringIO(raw), delimiter=separator, strict=True))
    except csv.Error as exc:
        raise CostImportError("Tabela CSV/TSV malformada.") from exc
    if not table:
        raise CostImportError("Arquivo sem linhas.")
    for row in table[:50]:
        if _find_header(row) is None:
            continue
        names = [_header(_cell(value)) for value in row if _cell(value)]
        if len(names) != len(set(names)):
            raise CostImportError("Cabeçalhos repetidos no arquivo.")
        break
    rows: list[CostCsvRow] = []
    _append_rows(rows, table)
    return rows


def _decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    if "\x00" in text:
        raise CostImportError("Documento de texto inválido.")
    if len(text) > MAX_TEXT_LENGTH:
        raise CostImportError("O texto extraído excede o limite permitido.")
    return text


def _from_labeled_lines(text: str, rows: list[CostCsvRow]) -> None:
    """Accept explicit SKU/reference + cost pairs; never infer an ID from a title."""
    code_expr = re.compile(
        r"(?i)\b(SKU|REFER[ÊE]NCIA|C[ÓO]D(?:IGO)?(?:\.?\s*ITEM)?|MLB)\s*[:=]\s*([A-Z0-9._:/-]+)")
    cost_expr = re.compile(
        r"(?i)\b(PRE[ÇC]O\s+M[ÉE]DIO|CUSTO(?:\s+(?:M[ÉE]DIO|UNIT[ÁA]RIO|DE\s+COMPRA))?)\s*[:=]\s*(?:R\$\s*)?([0-9.,]+)")
    for line in text.splitlines():
        identifier = code_expr.search(line)
        price = cost_expr.search(line)
        if not identifier or not price:
            continue
        label, key = identifier.group(1).upper(), identifier.group(2)
        index = {"cost": 1}
        if label == "MLB":
            index["mlb"] = 0
        elif label == "SKU" or "REFER" in label:
            index["reference"] = 0
        else:
            index["code"] = 0
        row = _record([key, price.group(2)], index, len(rows) + 2)
        if row:
            if len(rows) >= MAX_COST_IMPORT_ROWS:
                raise CostImportError("O documento excede 30.000 produtos.")
            rows.append(row)


def _from_text(text: str, rows: list[CostCsvRow]) -> None:
    if len(text) > MAX_TEXT_LENGTH:
        raise CostImportError("O texto extraído excede o limite permitido.")
    lines = [line for line in text.splitlines() if line.strip()]
    # Tables in exported PDFs often use multiple spaces instead of tabs.
    for separator in (";", "\t", "|", "multi_spaces"):
        table = []
        for line in lines:
            if separator == "multi_spaces":
                parts = re.split(r"\s{2,}", line.strip())
            elif separator in line:
                parts = next(csv.reader([line], delimiter=separator))
            else:
                continue
            table.append(parts)
        if any(_find_header(row) is not None for row in table):
            _append_rows(rows, table)
            return
    _from_labeled_lines(text, rows)


def _parse_docx(content: bytes) -> list[CostCsvRow]:
    from docx import Document
    _validated_zip(content)
    rows: list[CostCsvRow] = []
    try:
        document = Document(io.BytesIO(content))
        for table in document.tables:
            _append_rows(rows, [[cell.text for cell in line.cells] for line in table.rows])
        if not rows:
            _from_text("\n".join(p.text for p in document.paragraphs), rows)
    except CostImportError:
        raise
    except Exception as exc:
        raise CostImportError("Não foi possível ler o Word (.docx).") from exc
    return rows


def _parse_pdf(content: bytes) -> list[CostCsvRow]:
    import pdfplumber
    if not content.lstrip().startswith(b"%PDF-"):
        raise CostImportError("Arquivo PDF inválido.")
    rows: list[CostCsvRow] = []
    texts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            if len(document.pages) > MAX_PDF_PAGES:
                raise CostImportError("PDF muito longo; envie até 250 páginas por arquivo.")
            for page in document.pages:
                for table in page.extract_tables() or []:
                    _append_rows(rows, [list(line) for line in table])
                if not rows:
                    texts.append(page.extract_text(layout=True) or "")
    except CostImportError:
        raise
    except Exception as exc:
        raise CostImportError("Não foi possível ler o PDF.") from exc
    if not rows:
        _from_text("\n".join(texts), rows)
    return rows


def parse_cost_document(content: bytes, filename: str) -> list[CostCsvRow]:
    if not content or len(content) > MAX_COST_IMPORT_BYTES:
        raise CostImportError("Arquivo vazio ou maior que 16 MB.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise CostImportError("Formato não aceito. Envie CSV, TSV, Excel, Word (.docx), PDF ou TXT.")
    if suffix in (".csv", ".tsv"):
        rows = _parse_delimited(content, suffix)
    elif suffix == ".txt":
        rows = []
        _from_text(_decode_text(content), rows)
    elif suffix == ".xlsx":
        rows = _parse_xlsx(content)
    elif suffix == ".xls":
        rows = _parse_xls(content)
    elif suffix == ".docx":
        rows = _parse_docx(content)
    else:
        rows = _parse_pdf(content)
    if not rows:
        raise CostImportError(
            "Nenhum custo associado a SKU/Referência/MLB foi encontrado. "
            "O arquivo precisa de uma tabela com coluna de custo de compra ou Preço Médio, "
            "ou de linhas com SKU e custo explícitos. PDF digitalizado pode exigir OCR."
        )
    return rows
