from sqlalchemy.orm import sessionmaker

from app.database.connection import engine


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()