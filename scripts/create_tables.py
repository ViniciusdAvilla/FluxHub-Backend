from app.database.connection import engine
from app.database.models import Base

Base.metadata.create_all(engine)

print("Tabelas criadas com sucesso")
