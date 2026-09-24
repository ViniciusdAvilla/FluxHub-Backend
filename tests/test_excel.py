from app.connectors.excel.products import ExcelProducts


def test_excel_products():

    connector = ExcelProducts(
        "app/connectors/excel/data/shopping_dos_utilitarios_1000_produtos.xlsx"
    )

    products = connector.get_products()

    print()
    print("TOTAL DE PRODUTOS:", len(products))

    print()
    print("PRIMEIRO PRODUTO:")
    print(products[0])

    print()
    print("ÚLTIMO PRODUTO:")
    print(products[-1])