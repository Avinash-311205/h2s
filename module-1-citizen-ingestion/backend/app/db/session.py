from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models.request import CitizenRequest  # noqa: F401

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_database_schema() -> None:
    inspector = inspect(engine)
    if "citizen_requests" not in inspector.get_table_names():
        Base.metadata.create_all(bind=engine)
        return

    columns = {column["name"] for column in inspector.get_columns("citizen_requests")}
    if "geom" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE citizen_requests ADD COLUMN geom VARCHAR(255)"))


ensure_database_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
