import pandas as pd


data = [
    {
        "pedido_id": "1001",
        "produto_id": "PROD-001",
        "produto": "Kit Ferramentas",
        "quantidade": 2,
        "preco_unitario": 100.00,
        "status": "Pago",
    },
    {
        "pedido_id": "1002",
        "produto_id": "PROD-002",
        "produto": "Jogo de Chaves",
        "quantidade": 1,
        "preco_unitario": 249.90,
        "status": "Pago",
    },
    {
        "pedido_id": "1003",
        "produto_id": "PROD-001",
        "produto": "Kit Ferramentas",
        "quantidade": 3,
        "preco_unitario": 100.00,
        "status": "Enviado",
    },
]


df = pd.DataFrame(data)

df.to_excel(
    "tests/data/pedidos_ficticios.xlsx",
    index=False
)

print("Planilha criada com sucesso!")