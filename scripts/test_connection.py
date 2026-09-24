from app.database.connection import engine

print("Iniciando teste...")

with engine.connect() as conn:
    print("Conectado com sucesso!")

print("Fim")