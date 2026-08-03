from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import make_url

from style_rotation.persistence.base import SCHEMA_NAMES
from style_rotation.persistence.session import create_postgres_engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROTECTED_DATABASES = {"postgres", "template0", "template1"}
LOCAL_HOSTS = {None, "localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    database_name: str
    current_revision: str | None
    head_revisions: tuple[str, ...]
    present_schemas: tuple[str, ...]
    missing_schemas: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["database_url_override"] = database_url
    return config


def head_revisions(database_url: str) -> tuple[str, ...]:
    script = ScriptDirectory.from_config(alembic_config(database_url))
    return tuple(sorted(script.get_heads()))


def database_status(database_url: str) -> DatabaseStatus:
    engine = create_postgres_engine(database_url)
    try:
        return database_status_from_engine(engine, database_url)
    finally:
        engine.dispose()


def database_status_from_engine(engine: Engine, database_url: str) -> DatabaseStatus:
    available_schemas = set(inspect(engine).get_schema_names())
    present = tuple(item for item in SCHEMA_NAMES if item in available_schemas)
    missing = tuple(item for item in SCHEMA_NAMES if item not in available_schemas)
    with engine.connect() as connection:
        version_table_exists = connection.scalar(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        )
        current_revision = None
        if version_table_exists:
            current_revision = connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
    database_name = make_url(database_url).database
    if database_name is None:
        raise ValueError("Database URL must include a database name")
    return DatabaseStatus(
        database_name=database_name,
        current_revision=current_revision,
        head_revisions=head_revisions(database_url),
        present_schemas=present,
        missing_schemas=missing,
    )


def upgrade_database(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade_database(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)


def validate_reset_target(database_url: str, confirmation: str, environment: str) -> str:
    url = make_url(database_url)
    database_name = url.database
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Database reset requires PostgreSQL with psycopg")
    if url.host not in LOCAL_HOSTS:
        raise ValueError("Database reset is restricted to localhost")
    if environment not in {"local", "test"}:
        raise ValueError("Database reset is restricted to local or test environments")
    if database_name is None or database_name in PROTECTED_DATABASES:
        raise ValueError("Database reset target is missing or protected")
    if database_name != "style_rotation" and not database_name.startswith("style_rotation_"):
        raise ValueError("Database reset target must be project-scoped")
    if confirmation != database_name:
        raise ValueError("Reset confirmation must exactly match the database name")
    return database_name


def reset_database(database_url: str, confirmation: str, environment: str) -> None:
    validate_reset_target(database_url, confirmation, environment)
    engine = create_postgres_engine(database_url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            for schema_name in reversed(SCHEMA_NAMES):
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    upgrade_database(database_url)
