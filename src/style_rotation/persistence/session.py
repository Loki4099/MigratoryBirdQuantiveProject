from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_postgres_engine(database_url: str, *, echo: bool = False) -> Engine:
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("The formal database URL must use PostgreSQL with psycopg")
    return create_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
