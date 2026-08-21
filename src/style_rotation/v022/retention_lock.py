from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

# Session-scoped PostgreSQL advisory lock shared by Product promotion and
# Research Round GC.  A closed-round object cannot cross the Product strong-root
# boundary while its reachability is being checked and its bytes are evicted.
_V022_RETENTION_LOCK_KEY = 220_005_133


@contextmanager
def v022_retention_guard(engine: Engine) -> Iterator[None]:
    with engine.connect() as connection:
        connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": _V022_RETENTION_LOCK_KEY},
        )
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": _V022_RETENTION_LOCK_KEY},
            )
