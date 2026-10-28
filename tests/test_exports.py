from io import BytesIO

from openpyxl import load_workbook

from app.routers.exports import excel_cell, populate_sheet


def test_formula_strings_are_not_executed():
    assert excel_cell("=HYPERLINK(\"https://evil\", \"click\")").startswith("'=")
    assert excel_cell("+SUM(1,2)").startswith("'+")
    assert excel_cell("@SUM(1,2)").startswith("'@")
    assert excel_cell("Produto normal") == "Produto normal"


def test_export_sheet_writes_real_xlsx():
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    populate_sheet(wb, "Produtos", [
        ("MLB123", "=1+1", "CAT", "MARCA", 100, 90, 10, 2, "ml:123"),
    ])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    result = load_workbook(buf, read_only=True, data_only=False)
    assert result["Produtos"]["B2"].value.startswith("'=")
