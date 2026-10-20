from pathlib import Path

import pandas as pd

from app.schemas.product import Product
from app.connectors.base.connector import ProductSource


class ExcelProducts(ProductSource):
    def __init__(self, file_path: str, sheet_name: str = "Produtos_Ads"):
        self.file_path = Path(file_path)
        self.sheet_name = sheet_name

    def get_products(self) -> list[Product]:
        dataframe = pd.read_excel(
            self.file_path,
            sheet_name=self.sheet_name,
            engine="openpyxl",
        )

<<<<<<< HEAD
        # Manter o mapeamento atual e acrescentar os novos campos
        # ao schema Product e à persistência antes de importá-los.
    

=======
>>>>>>> 0f0be375cc57f2cc8768c8753476903afcddb5c7
        print(f"Arquivo: {self.file_path}")
        print(f"Linhas carregadas: {len(dataframe)}")

        products = []

        for _, row in dataframe.iterrows():

            product = Product(
                external_id=row["SKU"],
                title=row["Nome do Produto"],
                category=row["Categoria"],
                brand=row["Marca"],
                original_price=row["Preço Original (R$)"],
                promotional_price=row["Preço Promocional (R$)"],
                discount_rate=row["Desconto (%)"],
                stock_qty=row["Estoque Atual"],
                sales_30d=row["Vendas (30d)"],
                rating=row["Avaliação"],
                is_free_shipping=row["Frete Grátis"] == "Sim",
                item_condition=row["Condição"],
            )

            products.append(product)

        return products