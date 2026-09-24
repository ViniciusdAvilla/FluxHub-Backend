from sqlalchemy.orm import Session

from app.database.connection import engine
from app.database.repositories.products import ProductRepository

with Session(engine) as session:

    repo = ProductRepository(session)

    print(repo.count_products())